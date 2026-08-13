"""重建图谱：按新岗位归一化规则重放已抽取 JD（一次性数据修复）。

背景：岗位归一化策略调整（保留 React/Vue/小程序等技术栈细分）后，
已合并的岗位无法原地拆分，需从 jd_raw 已抽取记录重放 import_jd 重建图谱。

流程：
1. 清空图谱（保留 Counter 计数器节点，保证 ID 不重置）
2. 遍历 jd_raw 已抽取记录，import_jd 重放（岗位名经新归一化，空岗位跳过）
3. 重建后自动补跑与 ETL 一致的收尾阶段（幂等，可安全重跑）：
   - 课程入图（course_raw → Course + LEARNABLE_VIA）
   - 岗位聚合（Position.freq + REQUIRES weight/source_count）
   - 技能归一化 + SIMILAR_TO 建边（SBERT，模型不可用自动降级）
   - 技能关系建边（PREREQUISITE_OF / BELONGS_TO / ALTERNATIVE_OF）
   - 演化关系推导（EVOLVED_FROM，基于最近两版快照）
   - 发布当日图谱版本快照（graph_versions，幂等覆盖）

注意：本脚本会清空现有图谱数据（Evidence/Skill/Position 全部重建），
重建耗时取决于 JD 数量。请确认后执行。

用法：
    python scripts/rebuild_graph.py            # 交互确认后执行
    python scripts/rebuild_graph.py --yes      # 跳过确认直接执行
"""

import argparse
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
    """读取已抽取记录（排除 skipped：低质/过短标记的 extraction 不是有效结果）。"""
    async with async_session_factory() as s:
        rows = (await s.scalars(
            select(JDRaw).where(
                JDRaw.snapshot["extraction"].astext.isnot(None),
                JDRaw.snapshot["extraction"]["skipped"].astext.is_(None),
            )
        )).all()
    return [(r, (r.snapshot or {}).get("extraction") or {}) for r in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description="重建图谱（全库级破坏操作）")
    parser.add_argument("--yes", action="store_true", help="跳过交互确认")
    args = parser.parse_args()

    if not args.yes:
        confirm = input(
            "警告：将清空现有图谱全部节点（保留 Counter）并重建。输入 YES 继续: "
        )
        if confirm.strip() != "YES":
            logger.info("已取消，未做任何修改")
            return

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
    logger.info("JD 重放完成（跳过 SimHash 重复 %s 条）", skipped_dup)

    # 3. 收尾阶段（与 ETL 阶段 6/8/9.5/12.5/12.6/14 一致，幂等可重跑）：
    #    重建只重放 import_jd，若不补跑这些阶段，课程/关系边/归一化/快照
    #    在重建后全部缺失（08-12 重建丢失 Course 与五类关系的根因）。
    async def _post_rebuild() -> dict:
        from app.workers.tasks import (
            aggregate_positions,
            derive_evolved_from,
            load_courses,
            snapshot_graph,
            sync_skill_normalization,
        )
        from app.core.database import neo4j_driver as _neo4j_driver
        from app.services.kg.skill_relations import sync_skill_relations

        results = {}
        results["courses"] = await load_courses({})
        results["aggregate"] = await aggregate_positions({})
        results["normalization"] = await sync_skill_normalization({})

        def _relations() -> dict:
            with _neo4j_driver.session() as ns:
                return sync_skill_relations(ns)

        results["relations"] = await asyncio.to_thread(_relations)
        results["evolved"] = await derive_evolved_from()
        results["snapshot"] = await snapshot_graph({}, triggered_by="manual-rebuild")
        return results

    logger.info("补跑收尾阶段（课程/聚合/归一化/关系/演化/快照）...")
    summary = asyncio.run(_post_rebuild())
    for stage, detail in summary.items():
        logger.info("  [%s] %s", stage, detail)
    logger.info("重建全部完成（含收尾阶段），图谱可用")


if __name__ == "__main__":
    main()
