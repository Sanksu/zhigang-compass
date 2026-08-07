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
import sys
import time
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.workers.tasks import aggregate_positions, batch_extract

# 每批条数（tasks.py 注释：批量过大易触发 provider 限流，100 条/批实测稳定）
_BATCH_SIZE = 100
_MAX_ROUNDS = 15
# 等待回填结束：日志出现"完成："且文件大小连续两次采样不变
_BACKFILL_LOG = _BACKEND_DIR / "logs" / "backfill.log"
_WAIT_TIMEOUT_SECONDS = 6 * 3600
_POLL_SECONDS = 60


def _backfill_finished() -> bool:
    """回填完成判定：日志末尾出现"完成："（backfill_jd_detail 结束时打印）。"""
    if not _BACKFILL_LOG.exists():
        return False
    text = _BACKFILL_LOG.read_text(encoding="utf-8", errors="ignore").strip()
    if not text:
        return False
    return text.splitlines()[-1].startswith("完成：")


async def wait_backfill() -> None:
    """轮询等待回填完成；超时后继续（不阻塞重抽，防异常场景死等）。"""
    started = time.monotonic()
    while time.monotonic() - started < _WAIT_TIMEOUT_SECONDS:
        if _backfill_finished():
            # 连续两次采样文件大小不变，确认回填进程已停止写日志
            size1 = _BACKFILL_LOG.stat().st_size
            await asyncio.sleep(_POLL_SECONDS)
            if _backfill_finished() and _BACKFILL_LOG.stat().st_size == size1:
                print("[wait] 检测到回填完成，开始重抽")
                return
            continue
        await asyncio.sleep(_POLL_SECONDS)
    print(f"[wait] 等待回填完成超时（{_WAIT_TIMEOUT_SECONDS}s），继续重抽")


async def reingest() -> dict:
    """重抽：循环批量抽取（覆盖回填清除标记的记录）+ 岗位重聚合。"""
    rounds = 0
    succeeded = 0
    while rounds < _MAX_ROUNDS:
        rounds += 1
        r = await batch_extract({}, limit=_BATCH_SIZE)
        succeeded += r["succeeded"]
        print(
            f"[reingest round {rounds}] processed={r['processed']} "
            f"succeeded={r['succeeded']} failed={len(r['failed'])}"
        )
        if r["processed"] == 0:
            break
    agg = await aggregate_positions({})
    print(f"[reingest] 抽取轮数={rounds} 新增成功={succeeded} 聚合结果={agg}")
    return {"rounds": rounds, "succeeded": succeeded, "aggregate": agg}


async def main() -> None:
    print("[after_backfill_reingest] 启动，等待回填完成…")
    await wait_backfill()
    print("=== 阶段：重抽（batch_extract + aggregate_positions）===")
    result = await reingest()
    print(f"[after_backfill_reingest] 全部完成: {result}")


if __name__ == "__main__":
    asyncio.run(main())
