"""全量重抽脚本（M5 ② industry 抽取补强后，2026-08-12）。

背景：prompts.py 新增 industry 抽取规则（规则 8）前，JD 抽取的 industry 字段
全部为空，导致 Position.industry 为空/错位（领域匹配失效，domain_match 集成
测试失败）。本脚本分批调用 batch_extract（含 import_jd 入图，industry 非空
覆盖），重抽全部已抽取记录。

用法：
    uv run python scripts/re_extract_industry.py            # 全量（默认分批 250）
    uv run python scripts/re_extract_industry.py --limit 100  # 冒烟（前 100 条）

消耗：LLM 额度（4317 条约 1-1.5 小时，batch_size=8 并发 6）。
"""

import argparse
import asyncio
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("re_extract_industry")

_BATCH = 250  # 每批 jd_ids 数（batch_extract 内部再组 8 条/批 LLM 调用）


async def _run(limit: int | None) -> None:
    from sqlalchemy import func, select

    from app.core.database import async_session_factory
    from app.models.raw import JDRaw
    from app.workers.tasks import batch_extract

    async with async_session_factory() as session:
        total = (await session.execute(select(func.count()).select_from(JDRaw))).scalar_one()
    ids = []
    async with async_session_factory() as session:
        rows = (await session.scalars(select(JDRaw.id).order_by(JDRaw.id.asc()))).all()
        ids = list(rows)
    if limit:
        ids = ids[:limit]
    logger.info("全量重抽启动：%s 条（分批 %s）", len(ids), _BATCH)

    succeeded = failed = 0
    for i in range(0, len(ids), _BATCH):
        batch = ids[i:i + _BATCH]
        result = await batch_extract({"job_id": f"industry-re-extract-{i}"}, jd_ids=batch)
        succeeded += result.get("succeeded", 0)
        failed += len(result.get("failed", []))
        logger.info(
            "[%d/%d] 批 %s 条：成功 %s / 失败 %s（累计 成功 %s / 失败 %s）",
            i // _BATCH + 1, (len(ids) + _BATCH - 1) // _BATCH,
            len(batch), result.get("succeeded", 0), len(result.get("failed", [])),
            succeeded, failed,
        )
    logger.info("重抽完成：成功 %s / 失败 %s", succeeded, failed)


def main() -> None:
    parser = argparse.ArgumentParser(description="全量重抽（industry 补强后）")
    parser.add_argument("--limit", type=int, default=None, help="仅重抽前 N 条（冒烟）")
    args = parser.parse_args()
    asyncio.run(_run(args.limit))


if __name__ == "__main__":
    main()
