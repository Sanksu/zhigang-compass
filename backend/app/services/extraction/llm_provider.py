"""LLM Provider 链（M3 参考实现，算法红线：须算法岗张恺天逐行把关）。

对齐设计文档 §6.5 + AGENTS.md §6.4：
- 读取 `configs/llm_providers.yaml`（单一事实源），provider 为任意 OpenAI 兼容 API，
  可自由增删/调序；api_key 由管理员经 /admin/llm 后台填写并持久化到该文件
- 按 priority 升序、enabled 过滤，按顺序依次尝试
- instructor + Pydantic Schema 强校验（幻觉防控第一道防线），校验失败自动重试 1 次
- 同步路由 `call_sync`：单次尝试不重试，超时即抛 `LLMTimeoutError`（同步路由 10s 上限）
- 异步任务 `call_with_fallback`：按优先级依次尝试，单 provider 超时切下一个（30s × N 上限）
- 运维机制（§6.5）：429 触发指数退避（30s→60s→120s）、连续 3 次 5xx 熔断 5min，
  状态存 Redis（`llm:circuit:{name}` / `llm:backoff:{name}` / `llm:health:{name}`）；
  Redis 不可用时 fail-open（跳过退避/熔断检查，不阻塞调用链）

未配置 api_key 时抛 `LLMConfigurationError`（LLMExtractionError 子类），
jd_extractor 捕获后降级规则抽取，保证无 key 环境可运行。
"""

import logging
import os
import time
import urllib.request
from pathlib import Path
from typing import Optional, Type, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from app.services.extraction import llm_invocation

T = TypeVar("T", bound=BaseModel)

# 配置路径（相对 backend 根目录，与 admin.py 的 _LLM_CONFIG_PATH 一致）
_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "llm_providers.yaml"

# 同步/异步单 provider 超时（秒，对齐设计文档 §6.5）
SYNC_TIMEOUT_SECONDS = 10
ASYNC_TIMEOUT_SECONDS = 30
# 幻觉防控第一道防线：schema 校验失败重试次数（设计文档 §6.3）
VALIDATION_RETRIES = 1

# ---- 运维机制参数（设计文档 §6.5，与 configs/llm_providers.yaml failover/health_check 段一致）----
# 429 指数退避时长序列（s）：连续第 n 次限流退避 n 档，封顶末档 120s
BACKOFF_SECONDS = (30, 60, 120)
# 连续 5xx 达到该次数即熔断
CONSECUTIVE_5XX_TO_OPEN = 3
# 熔断窗口（s）：窗口内跳过该 provider，窗口过期自动进入探活（下次允许尝试）
CIRCUIT_BREAKER_WINDOW_SECONDS = 300
# 5xx 连续计数 TTL（s）：稀疏出现的 5xx（间隔超窗口）不累积到熔断
_5XX_COUNT_TTL_SECONDS = 600
# 健康检查单次探测超时（s）与结果 TTL（s，覆盖 2 个检查周期 5min×2）
HEALTH_CHECK_TIMEOUT_SECONDS = 10
_HEALTH_TTL_SECONDS = 600

_logger = logging.getLogger(__name__)


class LLMExtractionError(Exception):
    """LLM 抽取失败（超时/格式错误等）。"""


class LLMConfigurationError(LLMExtractionError):
    """未配置可用 provider（无 api_key / 全部禁用 / yaml 缺失）。"""


class LLMTimeoutError(LLMExtractionError):
    """单 provider 调用超时。"""


class LLMRateLimitError(LLMExtractionError):
    """provider 命中 429 限流（触发指数退避，§6.5）。"""


class LLMServerError(LLMExtractionError):
    """provider 返回 5xx（服务不可用，连续累计触发熔断，§6.5）。"""


# ---- Redis 状态存储抽象（熔断/退避/健康检查，§6.5）----
# 降级状态是纯增强能力：Redis 不可用/异常时 fail-open，不阻塞主调用链
# （退避/熔断检查按"未命中"处理，状态写入静默丢弃）
_redis_client = None


def _get_redis_client():
    """惰性创建同步 Redis 客户端。

    llm_provider 是同步模块，不能直接用 app.core.database.redis_client
    （redis.asyncio 客户端），此处自建同步连接；构造不发起连接，
    失败（无 Redis）由 _redis_* 的 except 兜底。
    """
    global _redis_client
    if _redis_client is None:
        from redis import Redis

        from app.core.config import settings

        # socket_timeout（第八轮 P2-17）：仅设 connect 超时只能约束握手，
        # 连接建立后命令挂起（Redis 主从切换/网络半开）会让 fail-open 的
        # 状态读写无限等待——补 socket_timeout=1 与 connect 超时同档兜底。
        _redis_client = Redis.from_url(
            settings.redis_url, socket_connect_timeout=1, socket_timeout=1,
        )
    return _redis_client


# instructor/OpenAI client 缓存（08-14 审查：每次调用重建 client，连接池无法复用）。
# 按 (base_url, api_key, mode, timeout) 复用；上限 32 防 timeout 变化导致的膨胀
_client_cache: dict = {}
_CLIENT_CACHE_MAX = 32


def _build_client(provider: dict, timeout: int):
    """构建/复用 instructor client（连接复用，批量/并发场景降低握手开销）。"""
    import instructor
    from openai import OpenAI

    # 逐行审查修复（2026-08-24）：与 _call_provider 同源解析——env-only provider
    # 此前直读明文得空串，真实调用链以空 key 构建 client（测试 monkeypatch 掉
    # _build_client 掩盖了该缺口，仅健康检查路径正确）
    api_key = _resolve_api_key(provider)
    # from_openai 函数身份入 key：测试 monkeypatch 换 fake 时不命中缓存（每次重建）
    key = (
        instructor.from_openai,
        provider["base_url"],
        api_key,
        provider.get("supports_function_calling", True),
        timeout,
    )
    client = _client_cache.get(key)
    if client is None:
        client = instructor.from_openai(
            OpenAI(base_url=key[1], api_key=key[2], timeout=timeout),
            mode=(
                instructor.Mode.TOOLS
                if key[3]
                else instructor.Mode.JSON_SCHEMA
            ),
        )
        if len(_client_cache) >= _CLIENT_CACHE_MAX:
            _client_cache.pop(next(iter(_client_cache)))
        _client_cache[key] = client
    return client


def _redis_get(key: str) -> Optional[str]:
    try:
        return _get_redis_client().get(key)
    except Exception:
        return None


def _redis_set(key: str, value: str, ttl: Optional[int] = None) -> None:
    try:
        _get_redis_client().set(key, value, ex=ttl)
    except Exception:
        pass


def _redis_delete(key: str) -> None:
    try:
        _get_redis_client().delete(key)
    except Exception:
        pass


def _redis_incr(key: str) -> int:
    """原子自增并返回新值（并发下不丢失计数）。

    覆盖 get-then-set 的竞态：多个 worker 同时命中 429/5xx 时，旧实现
    可能各自读到相同计数导致重复写入（熔断/退避档位低估）。返回 0
    表示 Redis 不可用（fail-open，调用方按未命中处理）。
    """
    try:
        return int(_get_redis_client().incr(key))
    except Exception:
        return 0


def _redis_expire(key: str, ttl: int) -> None:
    """刷新 key TTL（计数归零由 TTL 控制）。"""
    try:
        _get_redis_client().expire(key, ttl)
    except Exception:
        pass


def _now() -> float:
    """当前 epoch 秒（模块级可注入，测试替换为可控时钟）。"""
    return time.time()


def _state_key(kind: str, name: str) -> str:
    return f"llm:{kind}:{name}"


def _is_skipped(name: str) -> Optional[str]:
    """返回 provider 当前被跳过的原因（"circuit"/"backoff"），未命中返回 None。

    截止时间以 epoch 秒存于 Redis：key 缺失或已过期即不跳过
    （熔断窗口过期后自动进入探活，下次调用允许尝试）。
    """
    for kind in ("circuit", "backoff"):
        raw = _redis_get(_state_key(kind, name))
        if raw is not None:
            try:
                if _now() < float(raw):
                    return kind
            except ValueError:
                pass
    return None


def _record_429(name: str) -> None:
    """命中 429：退避档位 +1（30s→60s→120s 递进，封顶 120s）并写截止时间。

    退避 TTL 与退避期一致：key 过期即退避结束，无需清理任务；连续计数
    TTL 同为退避期，退避结束后计数归零，下次 429 从 30s 重新递进。
    """
    count_key = _state_key("backoff_count", name)
    count = _redis_incr(count_key)
    if count <= 0:
        return  # Redis 不可用，fail-open（调用链不依赖降级状态）
    delay = BACKOFF_SECONDS[min(count - 1, len(BACKOFF_SECONDS) - 1)]
    until = _now() + delay
    _redis_set(_state_key("backoff", name), str(until), ttl=int(delay))
    _redis_expire(count_key, int(delay))


def _record_5xx(name: str) -> None:
    """命中 5xx：连续计数 +1，达到 CONSECUTIVE_5XX_TO_OPEN 次即熔断 5min。

    计数 TTL 长于熔断窗口，窗口内计数不会自然过期；窗口过期后探活再次
    失败会从既有计数继续累计（连续失败语义）。熔断写入时计数清零。
    """
    count_key = _state_key("5xx_count", name)
    count = _redis_incr(count_key)
    if count <= 0:
        return  # Redis 不可用，fail-open（调用链不依赖降级状态）
    if count >= CONSECUTIVE_5XX_TO_OPEN:
        until = _now() + CIRCUIT_BREAKER_WINDOW_SECONDS
        _redis_set(
            _state_key("circuit", name), str(until), ttl=CIRCUIT_BREAKER_WINDOW_SECONDS
        )
        _redis_delete(count_key)
        _logger.warning(
            "provider '%s' 连续 %d 次 5xx，熔断 %ds",
            name, count, CIRCUIT_BREAKER_WINDOW_SECONDS,
        )
    else:
        _redis_expire(count_key, _5XX_COUNT_TTL_SECONDS)


def _clear_state(name: str) -> None:
    """调用成功：清除该 provider 的熔断/退避/计数状态（成功即恢复）。"""
    for kind in ("circuit", "backoff", "backoff_count", "5xx_count"):
        _redis_delete(_state_key(kind, name))


# ---- 健康检查（§6.5：每 5min 调 /models 端点验证可用性，结果写 Redis）----


def check_provider_health(provider: dict, timeout: Optional[int] = None) -> bool:
    """探测单个 provider 的 /models 端点（OpenAI 兼容）。

    GET `{base_url}/models`（Bearer api_key）：200 → healthy，否则 unhealthy。
    结果写 Redis（`llm:health:{name}`，TTL 覆盖 2 个检查周期）；
    Redis 不可用 / 网络异常均不抛异常（健康检查为旁路增强，探测结果仍返回）。
    """
    base_url = (provider.get("base_url") or "").rstrip("/")
    api_key = _resolve_api_key(provider)
    healthy = False
    if base_url:
        # 部分网关（如 opencode.ai）对无 UA 的 urllib 请求返回 403，需带浏览器 UA 探测
        req = urllib.request.Request(
            f"{base_url}/models",
            headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": "zhigang-compass/1.0",
            }
            if api_key
            else {"User-Agent": "zhigang-compass/1.0"},
        )
        try:
            with urllib.request.urlopen(
                req, timeout=timeout or HEALTH_CHECK_TIMEOUT_SECONDS
            ) as resp:
                healthy = resp.status == 200
        except Exception:
            healthy = False
    _redis_set(
        _state_key("health", provider.get("name", "?")),
        "1" if healthy else "0",
        ttl=_HEALTH_TTL_SECONDS,
    )
    return healthy


def health_check_all() -> dict:
    """遍历 enabled provider 执行健康检查，返回 {name: healthy}。

    配置缺失（无 yaml）时抛 `LLMConfigurationError`，由调度任务捕获后跳过。
    """
    chain = LLMProviderChain()
    return {p.get("name", "?"): check_provider_health(p) for p in chain._providers}


# ---- 调用审计（P0 可观测：每次尝试/跳过写 JSONL 明细 + Redis 聚合，旁路 fail-open）----

_OUTCOME_TIMEOUT = "timeout"
_OUTCOME_RATE_LIMITED = "rate_limited"
_OUTCOME_SERVER_ERROR = "server_error"
_OUTCOME_CONNECTION_ERROR = "connection_error"
_OUTCOME_VALIDATION_ERROR = "validation_error"
_OUTCOME_HTTP_4XX = "http_4xx"
_OUTCOME_CONFIG_ERROR = "config_error"
_OUTCOME_EXTRACTION_ERROR = "extraction_error"


def _outcome_of(exc: Optional[BaseException]) -> str:
    """异常 → 审计 outcome；优先取 _call_provider 附着的精确标记，按类型兜底。"""
    attached = getattr(exc, "outcome", None)
    if attached:
        return str(attached)
    if exc is None:
        return "ok"
    if isinstance(exc, LLMConfigurationError):
        return _OUTCOME_CONFIG_ERROR
    if isinstance(exc, LLMTimeoutError):
        return _OUTCOME_TIMEOUT
    if isinstance(exc, LLMRateLimitError):
        return _OUTCOME_RATE_LIMITED
    if isinstance(exc, LLMServerError):
        return _OUTCOME_SERVER_ERROR
    return _OUTCOME_EXTRACTION_ERROR


def _record_attempt(
    route: str,
    provider: dict,
    attempt: int,
    started: Optional[float],
    exc: Optional[BaseException] = None,
) -> None:
    """记录一次尝试（exc=None 为成功）或熔断/退避跳过事件（started=None）。"""
    llm_invocation.record(
        route=route,
        provider=str(provider.get("name", "?")),
        model=str(provider.get("model") or ""),
        attempt=attempt,
        outcome=_outcome_of(exc),
        duration_ms=0 if started is None else int((time.perf_counter() - started) * 1000),
        error=None if exc is None else str(exc),
    )


def _with_outcome(exc: LLMExtractionError, outcome: str) -> LLMExtractionError:
    """为审计附着精确 outcome 标记（调用方 _outcome_of 优先读取）。"""
    exc.outcome = outcome
    return exc


def _resolve_api_key(provider: dict) -> str:
    """provider api_key 解析：显式明文优先，其次 api_key_env 环境变量。

    推荐配置 api_key_env（如 OPENCODE_API_KEY）——密钥经环境变量注入，
    不落盘不回显；遗留明文 api_key 仍兼容（负责人拍板：key 走 env）。
    """
    explicit = (provider.get("api_key") or "").strip()
    if explicit:
        return explicit
    env_name = (provider.get("api_key_env") or "").strip()
    if env_name:
        return os.environ.get(env_name, "").strip()
    return ""


class LLMProviderChain:
    """多 provider 重试链。

    Args:
        config_path: yaml 配置路径，缺省为 `configs/llm_providers.yaml`（测试可注入）
    """

    def __init__(self, config_path: Optional[Path] = None):
        self._config_path = config_path or _CONFIG_PATH
        self._config = self._load_config()
        self._providers = self._load_providers()
        self._temperature = self._load_temperature()
        self._max_tokens = self._load_max_tokens()
        self._top_p = self._load_top_p()

    def _load_config(self) -> dict:
        """一次性读取 yaml（08-14 优化：原 4 个 _load_* 各自读文件，构造链读 4 次）。"""
        if not self._config_path.exists():
            raise LLMConfigurationError(f"LLM 配置缺失: {self._config_path}")
        try:
            return yaml.safe_load(self._config_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise LLMConfigurationError(f"LLM 配置解析失败: {e}") from e

    def _load_providers(self) -> list[dict]:
        """按 priority 升序返回 enabled 且 api_key 可解析的 provider。

        空 api_key 的 enabled provider 在构造时过滤（第八轮 P2-19）：
        真实调用链构建 client 时才解析 key，空 key provider 留在链上会
        白白消耗一轮尝试（同步路由直接失败、异步链路空转一个超时窗）。
        过滤后列表为空 → call_sync/call_with_fallback 维持既有
        LLMConfigurationError 语义（"未配置可用 provider"，模块头注释
        "未配置 api_key 时抛 LLMConfigurationError"）。
        """
        providers = [
            p for p in self._config.get("providers", [])
            if isinstance(p, dict)
        ]
        enabled = [p for p in providers if p.get("enabled")]
        with_key: list[dict] = []
        dropped: list[str] = []
        for p in enabled:
            if _resolve_api_key(p):
                with_key.append(p)
            else:
                dropped.append(str(p.get("name", "?")))
        if dropped:
            _logger.warning(
                "以下 enabled provider 的 api_key 解析为空，已从重试链过滤: %s",
                dropped,
            )
        return sorted(with_key, key=lambda p: p.get("priority", 99))

    def _load_temperature(self) -> float:
        """结构化输出温度（yaml `structured_output.temperature`，缺省 0.1）。

        配置值类型非法（如 "abc"）回落默认——配置缺省容忍，读取一次不重复
        解析（08-14 优化）。
        """
        try:
            return float(self._config.get("structured_output", {}).get("temperature", 0.1))
        except (TypeError, ValueError):
            return 0.1

    def _load_max_tokens(self) -> int:
        """结构化输出 max_tokens（yaml `structured_output.max_tokens`，缺省 2048）。"""
        try:
            return int(self._config.get("structured_output", {}).get("max_tokens", 2048))
        except (TypeError, ValueError):
            return 2048

    def _load_top_p(self) -> float:
        """结构化输出 top_p（yaml `structured_output.top_p`，缺省 0.9）。"""
        try:
            return float(self._config.get("structured_output", {}).get("top_p", 0.9))
        except (TypeError, ValueError):
            return 0.9

    # ---- 对外接口 ----

    def extract_structured(
        self,
        prompt: str,
        response_model: Type[T],
        max_retries: int = VALIDATION_RETRIES,
        system_prompt: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> T:
        """抽取接口（jd_extractor 兼容）：走异步语义的重试链。

        Args:
            system_prompt: 系统角色提示词（分层 Prompt，§6.2 设计）
            timeout: 单 provider 超时（秒）。缺省用 ASYNC_TIMEOUT_SECONDS；
                批量抽取输出 token 线性放大，需传更大独立超时（设计文档 6.5）
        """
        return self.call_with_fallback(
            prompt, response_model, max_retries=max_retries,
            system_prompt=system_prompt, timeout=timeout,
        )

    def call_sync(self, prompt: str, response_model: Type[T], system_prompt: Optional[str] = None) -> T:
        """同步路由：仅尝试优先级最高的 provider，超时即抛，不重试、不切换。

        供图谱查询/诊断报告等实时路径使用，调用方捕获 `LLMTimeoutError` 映射为
        504（错误码 5003，§2.4.7）；`LLMConfigurationError` 映射为 503。

        熔断/退避对同步路由同样生效：主 provider 处于窗口内时直接抛
        `LLMTimeoutError`（与"超时即 504"契约一致）；实际调用命中 429/5xx
        时记录状态后同样转 `LLMTimeoutError`，状态供后续异步路径退避/熔断。
        """
        if not self._providers:
            raise LLMConfigurationError("未配置可用 provider（无 api_key 或全部禁用）")
        provider = self._providers[0]
        name = provider.get("name", "?")
        skip_kind = _is_skipped(name)
        if skip_kind is not None:
            # 同步路由不重试不切换：直接按"LLM 暂时不可用"抛超时（上层映射 504，§6.5）
            _record_attempt("sync", provider, attempt=0, started=None,
                            exc=_with_outcome(
                                LLMTimeoutError(f"{skip_kind} 窗口跳过"),
                                f"{skip_kind}_skipped",
                            ))
            raise LLMTimeoutError(f"主 provider '{name}' 处于熔断/退避窗口，跳过")
        started = time.perf_counter()
        try:
            result = self._call_provider(
                provider, prompt, response_model,
                max_retries=0, timeout=SYNC_TIMEOUT_SECONDS, system_prompt=system_prompt,
            )
        except LLMRateLimitError as e:
            _record_429(name)
            _record_attempt("sync", provider, attempt=1, started=started, exc=e)
            raise LLMTimeoutError(str(e)) from e
        except LLMServerError as e:
            _record_5xx(name)
            _record_attempt("sync", provider, attempt=1, started=started, exc=e)
            raise LLMTimeoutError(str(e)) from e
        except LLMExtractionError as e:
            _record_attempt("sync", provider, attempt=1, started=started, exc=e)
            raise
        _clear_state(name)
        _record_attempt("sync", provider, attempt=1, started=started)
        return result

    def call_with_fallback(
        self,
        prompt: str,
        response_model: Type[T],
        max_retries: int = VALIDATION_RETRIES,
        system_prompt: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> T:
        """异步/批量路由：按优先级依次尝试，任一失败（超时/限流/5xx/连接/校验）切下一个。

        Args:
            timeout: 单 provider 超时（秒），缺省 ASYNC_TIMEOUT_SECONDS（30s）

        运维集成（§6.5）：尝试前查熔断/退避状态，命中则跳过；429 记录指数
        退避、5xx 累计熔断计数；调用成功清除该 provider 的熔断/退避状态。
        """
        if not self._providers:
            raise LLMConfigurationError("未配置可用 provider（无 api_key 或全部禁用）")
        effective_timeout = timeout or ASYNC_TIMEOUT_SECONDS
        failures: list[str] = []
        # 失败类别统计：区分"超时/不可用"（上层映射 504）与其余失败（503 语义）。
        # 全部 provider 均因超时/熔断/退避失败 → 抛 LLMTimeoutError，使同步/异步
        # 路径对"LLM 超时"映射 504（错误码 5003，§2.4.7）一致；连接/校验失败、
        # 限流、5xx 不算超时，维持父类 LLMExtractionError（上层映射 503）。
        timeout_like = 0
        wall_started = time.perf_counter()
        for index, provider in enumerate(self._providers, start=1):
            name = provider.get("name", "?")
            # 熔断/退避窗口内跳过该 provider，不发起调用（§6.5 运维机制）
            skip_kind = _is_skipped(name)
            if skip_kind is not None:
                _record_attempt("fallback", provider, attempt=0, started=None,
                                exc=_with_outcome(
                                    LLMTimeoutError(f"{skip_kind} 窗口跳过"),
                                    f"{skip_kind}_skipped",
                                ))
                failures.append(f"{name} 处于熔断/退避窗口，跳过")
                timeout_like += 1
                continue
            started = time.perf_counter()
            try:
                result = self._call_provider(
                    provider, prompt, response_model, max_retries,
                    effective_timeout, system_prompt,
                )
                _clear_state(name)  # 成功即恢复：解除该 provider 的熔断/退避计数
                _record_attempt("fallback", provider, attempt=index, started=started)
                # 链汇总（08-24 灰度底座）：整链总墙钟 + 最终 provider，仅 JSONL
                llm_invocation.record_chain(
                    provider=name, outcome="ok",
                    duration_ms=int((time.perf_counter() - wall_started) * 1000),
                )
                return result
            except LLMRateLimitError as e:
                _record_429(name)
                _record_attempt("fallback", provider, attempt=index, started=started, exc=e)
                failures.append(str(e))
                continue
            except LLMServerError as e:
                _record_5xx(name)
                _record_attempt("fallback", provider, attempt=index, started=started, exc=e)
                failures.append(str(e))
                continue
            except LLMTimeoutError as e:
                _record_attempt("fallback", provider, attempt=index, started=started, exc=e)
                failures.append(f"{name} 超时")
                timeout_like += 1
                continue
            except LLMExtractionError as e:
                # 连接/校验等其余抽取错误：非超时语义，交给上层按 503 处理
                _record_attempt("fallback", provider, attempt=index, started=started, exc=e)
                failures.append(str(e))
                continue
        llm_invocation.record_chain(
            provider="", outcome="failed",
            duration_ms=int((time.perf_counter() - wall_started) * 1000),
            error=" | ".join(failures)[:200] or None,
        )
        if timeout_like == len(self._providers):
            raise LLMTimeoutError("所有 provider 均超时/不可用: " + " | ".join(failures))
        raise LLMExtractionError("所有 provider 均失败: " + " | ".join(failures))

    # ---- 内部 ----

    @staticmethod
    def _build_messages(prompt: str, system_prompt: Optional[str]) -> list[dict]:
        """分层 Prompt（§6.2）：可选 system 角色 + user 任务输入。"""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return messages

    def _call_provider(
        self,
        provider: dict,
        prompt: str,
        response_model: Type[T],
        max_retries: int,
        timeout: int,
        system_prompt: Optional[str] = None,
    ) -> T:
        """调用单个 provider 的结构化抽取（instructor 强校验）。

        外部 API 调用是系统边界：任何调用异常都包装为 LLMExtractionError 子类，
        使重试链可继续尝试下一个 provider。429/5xx 单独区分（§6.5）：
        - 429（RateLimitError）→ LLMRateLimitError，触发指数退避
        - 5xx（APIStatusError, status_code>=500）→ LLMServerError，累计熔断
        - 其余（超时/连接/4xx/校验）保持既有 LLMTimeoutError/LLMExtractionError
        """
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            RateLimitError,
        )

        api_key = _resolve_api_key(provider)
        if not api_key:
            env_hint = f"（api_key_env={provider.get('api_key_env')} 未设置或为空）" if provider.get("api_key_env") else ""
            raise LLMConfigurationError(
                f"provider '{provider['name']}' 未配置 api_key{env_hint}"
            )

        client = _build_client(provider, timeout)
        try:
            return client.chat.completions.create(
                model=provider["model"],
                response_model=response_model,
                messages=self._build_messages(prompt, system_prompt),
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                top_p=self._top_p,
                max_retries=max_retries,
                # provider 特定请求参数透传（如 deepseek-v4-flash 关闭思考模式:
                # {"thinking": {"type": "disabled"}}，见 configs/llm_providers.yaml）
                extra_body=provider.get("extra_body") or None,
            )
        except APITimeoutError as e:
            raise _with_outcome(
                LLMTimeoutError(f"provider '{provider['name']}' 超时（{timeout}s）"),
                _OUTCOME_TIMEOUT,
            ) from e
        except RateLimitError as e:
            raise _with_outcome(
                LLMRateLimitError(f"provider '{provider['name']}' 触发限流(429): {e}"),
                _OUTCOME_RATE_LIMITED,
            ) from e
        except APIStatusError as e:
            if e.status_code >= 500:
                raise _with_outcome(
                    LLMServerError(
                        f"provider '{provider['name']}' 服务不可用({e.status_code}): {e}"
                    ),
                    _OUTCOME_SERVER_ERROR,
                ) from e
            raise _with_outcome(
                LLMExtractionError(f"provider '{provider['name']}' 调用失败: {e}"),
                _OUTCOME_HTTP_4XX,
            ) from e
        except APIConnectionError as e:
            raise _with_outcome(
                LLMExtractionError(f"provider '{provider['name']}' 连接失败: {e}"),
                _OUTCOME_CONNECTION_ERROR,
            ) from e
        except Exception as e:  # 外部 API/校验异常统一包装，交给重试链
            # 校验失败细分：instructor 重试耗尽（InstructorRetryException）与
            # 裸 Pydantic 校验错误 → validation_error；其余保持 extraction_error
            outcome = _OUTCOME_EXTRACTION_ERROR
            try:
                from instructor.exceptions import InstructorRetryException

                if isinstance(e, (InstructorRetryException,)):
                    outcome = _OUTCOME_VALIDATION_ERROR
            except ImportError:
                pass
            if isinstance(e, ValidationError):
                outcome = _OUTCOME_VALIDATION_ERROR
            raise _with_outcome(
                LLMExtractionError(f"provider '{provider['name']}' 调用异常: {e}"),
                outcome,
            ) from e
