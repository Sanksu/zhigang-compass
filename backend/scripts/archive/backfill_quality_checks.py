"""补全全量 JD 的时滞/通胀检测（设计文档 §4.7/§4.8）。

validate_temporal / detect_inflation 的游标为 `snapshot["validation"]/["inflation"] is None`，
循环调用会在"数据不足被跳过的 JD"上卡死（跳过不写标记，下次仍被选中）。
本脚本一次性查询全部未检测 JD id，分批显式传入（每批只处理一次），绕开游标死循环，
让全量已抽取 JD 的降权系数（decay_weight）生效。

用法：uv run python scripts/backfill_quality_checks.py [--batch-size 500]
"""

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.logging import setup_logging

logger = setup_logging("backfill_quality_checks")

from app.core.database import async_session_factory
from app.models.raw import JDRaw
from app.workers.tasks import detect_inflation, validate_temporal


def _chunks(items: list[int], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


async def _pending_ids(key: str) -> list[int]:
    """已抽取但未做 key（validation/inflation）检测的 JD id（升序）。"""
    async with async_session_factory() as session:
        rows = await session.scalars(
            select(JDRaw.id)
            .where(
                JDRaw.snapshot["extraction"].astext.isnot(None),
                JDRaw.snapshot[key].astext.is_(None),
            )
            .order_by(JDRaw.id.asc())
        )
        return list(rows)


async def main(batch_size: int) -> None:
    for key, task in (("validation", validate_temporal), ("inflation", detect_inflation)):
        ids = await _pending_ids(key)
        logger.info(f"[{key}] 待补全 JD: {len(ids)}")
        checked = skipped = flagged = 0
        for i, batch in enumerate(_chunks(ids, batch_size), 1):
            # 任务内部 limit 默认 200，显式传 len(batch) 确保整批处理，
            # 否则每批只处理前 200 个 id，剩余被漏掉
            result = await task({}, jd_ids=batch, limit=len(batch))
            checked += result["checked"]
            skipped += result["skipped"]
            flagged += len(result["flagged"])
            logger.info(
                f"  批次 {i}/{max(1, (len(ids) + batch_size - 1) // batch_size)}: "
                f"checked={result['checked']} skipped={result['skipped']} "
                f"flagged={len(result['flagged'])}"
            )
        logger.info(f"[{key}] 完成: checked={checked} skipped={skipped} flagged={flagged}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="补全全量 JD 的时滞/通胀检测")
    parser.add_argument("--batch-size", type=int, default=500, help="每批传入任务的 JD 数")
    args = parser.parse_args()
    asyncio.run(main(args.batch_size))
