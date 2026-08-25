"""LLM 调用审计记录（P0 前置阻塞：统一调用可观测）。

每次 provider 链调用（成功/失败/跳过）产出一条 LLMInvocationRecord：
- JSONL 明细：backend/reports/llm_invocations/{date}.jsonl（reports 已 gitignore，
  与 dict_guard 巡检报告同目录约定）
- Redis 聚合计数：llm:stats:{ymd}:{provider}:{outcome} / calls_total / latency_ms_sum
  （TTL 8 天，供管理端与运维快速读取当日成功率/时延）

设计约束（对齐 llm_provider fail-open 语义）：
1. 审计是旁路增强——Redis 不可用 / 磁盘不可写均静默降级，绝不阻塞或影响调用链；
2. purpose 由调用方经 invocation_scope() 声明（contextvar），provider 链零签名改动，
   未声明时记录 "unspecified"；
3. 线程安全：JSONL 追加持进程内锁；asyncio.to_thread 会复制 contextvars，
   线程池路径的 purpose 同样生效。

运行上下文（08-24 灰度底座）：invocation_scope 可携带 run_id / version /
entity_ref / env 覆盖——维测与回放依赖这些字段定位"哪批、哪个实体、哪个
prompt/schema 版本"产生的结果；env 未显式声明时按 pytest 进程 → "test"，
否则 APP_ENV → "production"，实现测试/生产日志隔离。
"""

import json
import logging
import os
import sys
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 东八区（与 workers/dict_guard 报告日期口径一致）
_CST = timezone(timedelta(hours=8))

# JSONL 落盘根目录：backend/reports/llm_invocations/
# （parents[3] = backend/，模块属性可注入以便测试指向 tmp_path）
_SINK_DIR = Path(__file__).resolve().parents[3] / "reports" / "llm_invocations"

# 进程内追加锁 + 首次写失败后停用标记（避免每日数千次重复报错日志）
_append_lock = threading.Lock()
_sink_disabled = False

_purpose: ContextVar[str] = ContextVar("llm_invocation_purpose", default="unspecified")
_run_id: ContextVar[str] = ContextVar("llm_invocation_run_id", default="")
_version: ContextVar[str] = ContextVar("llm_invocation_version", default="")
_entity_ref: ContextVar[str] = ContextVar("llm_invocation_entity_ref", default="")
_env: ContextVar[str] = ContextVar("llm_invocation_env", default="")


@contextmanager
def invocation_scope(
    purpose: str,
    *,
    run_id: str = "",
    version: str = "",
    entity_ref: str = "",
    env: str = "",
):
    """声明当前调用链的用途标签与运行上下文（供审计记录维度）。

    用法：`with invocation_scope("jd_extract", run_id=..., version="v3"): ...`
    可选维度（未传则回退默认）：run_id 批/轮次标识、version prompt/schema 版本
    标签、entity_ref 实体引用（如 jd:{id}）、env 环境覆盖（缺省自动判 test/prod）。
    asyncio.to_thread 复制 contextvars，线程池内调用同样生效；
    退出时 reset 回外层值，不向后续调用泄漏。
    """
    tokens = [
        _purpose.set(purpose or "unspecified"),
        _run_id.set(run_id or ""),
        _version.set(version or ""),
        _entity_ref.set(entity_ref or ""),
        _env.set(env or ""),
    ]
    try:
        yield
    finally:
        for token in reversed(tokens):
            token.var.reset(token)


def current_purpose() -> str:
    return _purpose.get()


def current_run_id() -> str:
    return _run_id.get()


def current_version() -> str:
    return _version.get()


def current_entity_ref() -> str:
    return _entity_ref.get()


def _current_env() -> str:
    """环境判定：显式覆盖 > pytest 进程（test）> APP_ENV（production）。"""
    override = _env.get()
    if override:
        return override
    if "pytest" in sys.modules:
        return "test"
    return os.environ.get("APP_ENV", "production")


def record(
    *,
    route: str,
    provider: str,
    model: str,
    attempt: int,
    outcome: str,
    duration_ms: int,
    error: Optional[str] = None,
) -> None:
    """记录一次 provider 尝试（成功/失败/熔断跳过）。

    route: sync | fallback；outcome 见 llm_provider._OUTCOME_*；
    attempt 从 1 计（0 = 未发起调用的熔断/退避跳过事件）。
    运行上下文（run_id/version/entity_ref/env）自动从 invocation_scope 带入。
    """
    entry = {
        "ts": datetime.now(_CST).isoformat(timespec="milliseconds"),
        "route": route,
        "purpose": current_purpose(),
        "provider": provider,
        "model": model,
        "attempt": attempt,
        "outcome": outcome,
        "duration_ms": duration_ms,
        "error": (error or "")[:200] or None,
        "run_id": current_run_id(),
        "version": current_version(),
        "entity_ref": current_entity_ref(),
        "env": _current_env(),
    }
    _incr_redis_counters(entry)
    _append_jsonl(entry)


def record_chain(
    *,
    provider: str,
    outcome: str,
    duration_ms: int,
    error: Optional[str] = None,
) -> None:
    """记录一次 provider 链整条汇总（总墙钟 + 最终 provider）。

    仅写 JSONL 明细，**不写 Redis 计数**——避免把整链耗时混入单 provider
    的 latency 均值（per-provider 统计只应含真实尝试行）。purpose/上下文
    与 record() 同源；provider 传最终成功 provider 或 ""（全链失败）。
    """
    entry = {
        "ts": datetime.now(_CST).isoformat(timespec="milliseconds"),
        "route": "chain",
        "purpose": current_purpose(),
        "provider": provider,
        "model": "",
        "attempt": 0,
        "outcome": outcome,
        "duration_ms": duration_ms,
        "error": (error or "")[:200] or None,
        "run_id": current_run_id(),
        "version": current_version(),
        "entity_ref": current_entity_ref(),
        "env": _current_env(),
    }
    _append_jsonl(entry)


# ---- Redis 聚合计数（fail-open：任何异常静默吞掉，不阻塞调用链）----

_redis_client = None


def _get_redis():
    """惰性同步 Redis 客户端（镜像 llm_provider 自建客户端模式）。"""
    global _redis_client
    if _redis_client is None:
        from redis import Redis

        from app.core.config import settings

        _redis_client = Redis.from_url(settings.redis_url, socket_connect_timeout=1)
    return _redis_client


_STATS_TTL_SECONDS = 8 * 24 * 3600


def _stats_day() -> str:
    return datetime.now(_CST).strftime("%Y-%m-%d")


def _incr_redis_counters(entry: dict) -> None:
    try:
        client = _get_redis()
        day, provider, outcome = entry["ts"][:10], entry["provider"], entry["outcome"]
        base = f"llm:stats:{day}:{provider}"
        pipe = client.pipeline(transaction=False)
        pipe.incr(f"{base}:{outcome}")
        pipe.incr(f"{base}:calls_total")
        pipe.incrby(f"{base}:latency_ms_sum", int(entry["duration_ms"]))
        for key in (
            f"{base}:{outcome}", f"{base}:calls_total",
            f"{base}:latency_ms_sum",
        ):
            pipe.expire(key, _STATS_TTL_SECONDS)
        pipe.execute()
    except Exception:
        pass


# ---- JSONL 明细落盘（best-effort，首次失败即停用并告警一次）----


def _append_jsonl(entry: dict) -> None:
    global _sink_disabled
    if _sink_disabled:
        return
    try:
        line = json.dumps(entry, ensure_ascii=False)
        with _append_lock:
            _SINK_DIR.mkdir(parents=True, exist_ok=True)
            path = _SINK_DIR / f"{entry['ts'][:10]}.jsonl"
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as e:
        _sink_disabled = True
        logger.warning("LLM 调用审计 JSONL 写入失败，本进程停用明细落盘: %s", e)
