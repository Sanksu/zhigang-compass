"""重建图谱：按新岗位归一化规则重放已抽取 JD（一次性数据修复）。

背景：岗位归一化策略调整（保留 React/Vue/小程序等技术栈细分）后，
已合并的岗位无法原地拆分，需从 jd_raw 已抽取记录重放 import_jd 重建图谱。

流程：
1. 清空图谱（保留 Counter 计数器节点，保证 ID 不重置）
2. 遍历 jd_raw 已抽取记录，import_jd 重放（岗位名经新归一化，空岗位跳过）
3. 之后请运行 scripts/cleanup_graph.py 做技能过滤 + 岗位合并 + 重新聚合

注意：本脚本会清空现有图谱数据（Evidence/Skill/Position 全部重建），
重建耗时取决于 JD 数量。请确认后执行。

用法：
    python scripts/rebuild_graph.py
"""

import asyncio
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("rebuild_graph")

from sqlalchemy import select

from app.core.database import async_session_factory, neo4j_driver
from app.models.raw import JDRaw
from app.services.extraction.schemas import JDExtractionResult
from app.services.kg.kg_service import import_jd


async def _load_extracted() -> list[tuple[JDRaw, dict]]:
    async with async_session_factory() as s:
        rows = (await s.scalars(
            select(JDRaw).where(JDRaw.snapshot["extraction"].astext.isnot(None))
        )).all()
    return [(r, (r.snapshot or {}).get("extraction") or {}) for r in rows]


def main() -> None:
    # 1. 清空图谱（保留 Counter）
    with neo4j_driver.session() as session:
        cleared = session.run(
            "MATCH (n) WHERE NOT n:Counter DETACH DELETE n RETURN count(n) AS c"
        ).single()["c"]
        logger.info(f"清空图谱节点 {cleared} 个（保留 Counter）")

    # 2. 重放 import_jd
    pairs = asyncio.run(_load_extracted())
    logger.info(f"重放 {len(pairs)} 条已抽取 JD ...")
    skipped_dup = 0
    with neo4j_driver.session() as session:
        for i, (row, ext) in enumerate(pairs, 1):
            # SimHash 重复记录（snapshot._duplicate_of）不重放：聚合同样跳过它们，
            # 否则会重建出聚合不覆盖的 REQUIRES 边（无 source_count 的残留边）。
            # 产品链路抽取后先入图、去重后补标记，故重复判定只能在重放层做。
            if (row.snapshot or {}).get("_duplicate_of"):
                skipped_dup += 1
                continue
            try:
                extraction = JDExtractionResult.model_validate(ext)
            except Exception as e:
                logger.exception("  [%s] 跳过（抽取结果非法）: %s %s", i, row.id, str(e)[:100])
                continue
            evidence = {
                "source": row.source,
                "source_url": row.source_url,
                "crawled_at": row.crawled_at,
                "raw_text": (row.snapshot or {}).get("raw_text", "") or row.raw_text or "",
            }
            try:
                import_jd(session, extraction, evidence)
            except Exception as e:
                logger.exception("  [%s] 入图失败: %s %s", i, row.id, str(e)[:150])
            if i % 100 == 0:
                logger.info("  已处理 %s 条", i)
    logger.info("重建完成（跳过 SimHash 重复 %s 条）。请运行 cleanup_graph.py 做技能过滤与聚合。", skipped_dup)


if __name__ == "__main__":
    main()
