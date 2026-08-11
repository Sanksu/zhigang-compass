"""回填完成后自动触发重抽（batch_extract + aggregate_positions）。

背景：backfill_jd_detail 只补详情正文并删除 snapshot.extraction 标记，
不会自动重抽。本脚本等待回填运行结束（日志出现"完成："且文件停止增长），
然后：
- 阶段 1：循环调用 batch_extract（每批 100 条）直到无未抽取 JD（复用
  backfill_ingest 模式；被回填清除 extraction 标记的记录在此用完整正文重抽）
- 阶段 2：aggregate_positions 全量重聚合写回 Neo4j（幂等）

用法（后台运行）：
    uv run python -u scripts/after_backfill_reingest.py *> logs/reingest_auto.log
"""

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

# 环境变量须在第三方库导入前设置（HF 未登录告警 / telemetry）
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")

from app.core.logging import setup_logging

logger = setup_logging("after_backfill_reingest")

from app.workers.tasks import aggregate_positions, batch_extract
import app.core.database  # 显式触发 create_async_engine(echo=settings.debug)（tasks 内为惰性导入）

# ── 噪音抑制（须在引擎创建之后）──
# echo 会为 sqlalchemy.engine.Engine 追加 StreamHandler 并绕过 logger 级别检查直接驱动输出，
# 须移除 handler 而非仅 setLevel（诊断验证）；HF/Neo4j 走标准 logger，setLevel 即可。
def _quiet_logger(name: str) -> None:
    lg = logging.getLogger(name)
    lg.setLevel(logging.WARNING)
    for h in list(lg.handlers):
        lg.removeHandler(h)


_quiet_logger("sqlalchemy.engine")
_quiet_logger("sqlalchemy.engine.Engine")
logging.getLogger("neo4j").setLevel(logging.ERROR)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
logging.getLogger("sentence_transformers").setLevel(logging.ERROR)

# 每批条数（tasks.py 注释：批量过大易触发 provider 限流，100 条/批实测稳定）
_BATCH_SIZE = 100
# 轮次安全上限（防异常死循环；全库 no_ext ~2335 条需 24 轮，留足余量）
_MAX_ROUNDS = 60
# 等待回填结束：日志出现"完成："且文件大小连续两次采样不变
_BACKFILL_LOG = _BACKEND_DIR / "logs" / "backfill.log"
_WAIT_TIMEOUT_SECONDS = 6 * 3600
_POLL_SECONDS = 60


def _backfill_finished() -> bool:
    """回填完成判定：日志末尾含"完成："（backfill_jd_detail 结束时打印）。

    日志经统一格式输出（`%(asctime)s %(levelname)s [%(name)s] %(message)s`），
    行首为时间戳，须用 `in` 而非 `startswith` 匹配。
    """
    if not _BACKFILL_LOG.exists():
        return False
    text = _BACKFILL_LOG.read_text(encoding="utf-8", errors="ignore").strip()
    if not text:
        return False
    return "完成：" in text.splitlines()[-1]


async def wait_backfill() -> None:
    """轮询等待回填完成；超时后继续（不阻塞重抽，防异常场景死等）。"""
    started = time.monotonic()
    while time.monotonic() - started < _WAIT_TIMEOUT_SECONDS:
        if _backfill_finished():
            # 连续两次采样文件大小不变，确认回填进程已停止写日志
            size1 = _BACKFILL_LOG.stat().st_size
            await asyncio.sleep(_POLL_SECONDS)
            if _backfill_finished() and _BACKFILL_LOG.stat().st_size == size1:
                logger.info("[wait] 检测到回填完成，开始重抽")
                return
            continue
        await asyncio.sleep(_POLL_SECONDS)
    logger.warning("[wait] 等待回填完成超时（%ss），继续重抽", _WAIT_TIMEOUT_SECONDS)


def _progress_bar(done: int, total: int, width: int = 30) -> str:
    """文本进度条（日志友好，无控制字符）。"""
    ratio = min(done / total, 1.0) if total else 1.0
    filled = int(ratio * width)
    return f"[{'#' * filled}{'-' * (width - filled)}] {ratio:.0%}"


async def _count_no_ext() -> int:
    """当前未抽取记录数（与 batch_extract 判定一致：snapshot 无 extraction 键）。"""
    from sqlalchemy import func, select

    from app.core.database import async_session_factory
    from app.models.raw import JDRaw

    async with async_session_factory() as session:
        return (
            await session.scalar(
                select(func.count())
                .select_from(JDRaw)
                .where(JDRaw.snapshot["extraction"].astext.is_(None))
            )
        ) or 0


async def reingest() -> dict:
    """重抽：循环批量抽取（覆盖回填清除标记的记录）+ 岗位重聚合。"""
    rounds = 0
    succeeded = 0
    remaining = await _count_no_ext()
    total = remaining
    total_rounds = max(1, (total + _BATCH_SIZE - 1) // _BATCH_SIZE)
    started = time.monotonic()
    logger.info("[reingest] 待抽取 %s 条，预计 %s 轮", total, total_rounds)
    while rounds < _MAX_ROUNDS:
        rounds += 1
        r = await batch_extract({}, limit=_BATCH_SIZE)
        succeeded += r["succeeded"]
        remaining = await _count_no_ext()
        elapsed = time.monotonic() - started
        eta_min = elapsed / rounds * (total_rounds - rounds) / 60 if rounds else 0
        logger.info(
            "[reingest round %s/%s] %s | 已抽 %s 条 | 剩余 %s 条 | "
            "本轮 成功 %s 失败 %s | 用时 %.1fmin 预计剩余 %.1fmin",
            rounds, total_rounds, _progress_bar(rounds, total_rounds), succeeded,
            remaining, r["succeeded"], len(r["failed"]), elapsed / 60, eta_min,
        )
        if r["processed"] == 0:
            break
    agg = await aggregate_positions({})
    logger.info("[reingest] 抽取轮数=%s 新增成功=%s 聚合结果=%s", rounds, succeeded, agg)
    return {"rounds": rounds, "succeeded": succeeded, "aggregate": agg}


async def main() -> None:
    logger.info("[after_backfill_reingest] 启动，等待回填完成…")
    await wait_backfill()
    logger.info("=== 阶段：重抽（batch_extract + aggregate_positions）===")
    result = await reingest()
    logger.info("[after_backfill_reingest] 全部完成: %s", result)


if __name__ == "__main__":
    asyncio.run(main())
