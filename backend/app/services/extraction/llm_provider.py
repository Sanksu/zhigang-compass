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
import time
import urllib.request
from pathlib import Path
from typing import Optional, Type, TypeVar

import yaml
from pydantic import BaseModel

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

        _redis_client = Redis.from_url(settings.redis_url, socket_connect_timeout=1)
    return _redis_client


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
    api_key = (provider.get("api_key") or "").strip()
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


class LLMProviderChain:
    """多 provider 重试链。

    Args:
        config_path: yaml 配置路径，缺省为 `configs/llm_providers.yaml`（测试可注入）
    """

    def __init__(self, config_path: Optional[Path] = None):
        self._config_path = config_path or _CONFIG_PATH
        self._providers = self._load_providers()
        self._temperature = self._load_temperature()
        self._max_tokens = self._load_max_tokens()
        self._top_p = self._load_top_p()

    def _load_providers(self) -> list[dict]:
        """读取 yaml 并按 priority 升序返回 enabled provider。"""
        if not self._config_path.exists():
            raise LLMConfigurationError(f"LLM 配置缺失: {self._config_path}")
        try:
            data = yaml.safe_load(self._config_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            raise LLMConfigurationError(f"LLM 配置解析失败: {e}") from e
        providers = [
            p for p in data.get("providers", [])
            if isinstance(p, dict)
        ]
        enabled = [p for p in providers if p.get("enabled")]
        return sorted(enabled, key=lambda p: p.get("priority", 99))

    def _load_temperature(self) -> float:
        """结构化输出温度（yaml `structured_output.temperature`，缺省 0.1）。"""
        try:
            data = yaml.safe_load(self._config_path.read_text(encoding="utf-8")) or {}
            return float(data.get("structured_output", {}).get("temperature", 0.1))
        except (OSError, yaml.YAMLError, TypeError, ValueError):
            return 0.1

    def _load_max_tokens(self) -> int:
        """结构化输出 max_tokens（yaml `structured_output.max_tokens`，缺省 2048）。

        设计文档 §6.2：max_tokens = 2048（与 temperature 同源的读取模式）。
        """
        try:
            data = yaml.safe_load(self._config_path.read_text(encoding="utf-8")) or {}
            return int(data.get("structured_output", {}).get("max_tokens", 2048))
        except (OSError, yaml.YAMLError, TypeError, ValueError):
            return 2048

    def _load_top_p(self) -> float:
        """结构化输出 top_p（yaml `structured_output.top_p`，缺省 0.9）。

        设计文档 §6.2：top_p = 0.9（与 temperature 同源的读取模式）。
        """
        try:
            data = yaml.safe_load(self._config_path.read_text(encoding="utf-8")) or {}
            return float(data.get("structured_output", {}).get("top_p", 0.9))
        except (OSError, yaml.YAMLError, TypeError, ValueError):
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
        if _is_skipped(name) is not None:
            # 同步路由不重试不切换：直接按"LLM 暂时不可用"抛超时（上层映射 504，§6.5）
            raise LLMTimeoutError(f"主 provider '{name}' 处于熔断/退避窗口，跳过")
        try:
            result = self._call_provider(
                provider, prompt, response_model,
                max_retries=0, timeout=SYNC_TIMEOUT_SECONDS, system_prompt=system_prompt,
            )
        except LLMRateLimitError as e:
            _record_429(name)
            raise LLMTimeoutError(str(e)) from e
        except LLMServerError as e:
            _record_5xx(name)
            raise LLMTimeoutError(str(e)) from e
        _clear_state(name)
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
        for provider in self._providers:
            name = provider.get("name", "?")
            # 熔断/退避窗口内跳过该 provider，不发起调用（§6.5 运维机制）
            if _is_skipped(name) is not None:
                failures.append(f"{name} 处于熔断/退避窗口，跳过")
                timeout_like += 1
                continue
            try:
                result = self._call_provider(
                    provider, prompt, response_model, max_retries,
                    effective_timeout, system_prompt,
                )
                _clear_state(name)  # 成功即恢复：解除该 provider 的熔断/退避计数
                return result
            except LLMRateLimitError as e:
                _record_429(name)
                failures.append(str(e))
                continue
            except LLMServerError as e:
                _record_5xx(name)
                failures.append(str(e))
                continue
            except LLMTimeoutError:
                failures.append(f"{name} 超时")
                timeout_like += 1
                continue
            except LLMExtractionError as e:
                # 连接/校验等其余抽取错误：非超时语义，交给上层按 503 处理
                failures.append(str(e))
                continue
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
        import instructor
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            OpenAI,
            RateLimitError,
        )

        api_key = (provider.get("api_key") or "").strip()
        if not api_key:
            raise LLMConfigurationError(f"provider '{provider['name']}' 未配置 api_key")

        client = instructor.from_openai(
            OpenAI(base_url=provider["base_url"], api_key=api_key, timeout=timeout),
            mode=(
                instructor.Mode.TOOLS
                if provider.get("supports_function_calling", True)
                else instructor.Mode.JSON_SCHEMA
            ),
        )
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
            raise LLMTimeoutError(f"provider '{provider['name']}' 超时（{timeout}s）") from e
        except RateLimitError as e:
            raise LLMRateLimitError(f"provider '{provider['name']}' 触发限流(429): {e}") from e
        except APIStatusError as e:
            if e.status_code >= 500:
                raise LLMServerError(
                    f"provider '{provider['name']}' 服务不可用({e.status_code}): {e}"
                ) from e
            raise LLMExtractionError(f"provider '{provider['name']}' 调用失败: {e}") from e
        except APIConnectionError as e:
            raise LLMExtractionError(f"provider '{provider['name']}' 连接失败: {e}") from e
        except Exception as e:  # 外部 API/校验异常统一包装，交给重试链
            raise LLMExtractionError(f"provider '{provider['name']}' 调用异常: {e}") from e
