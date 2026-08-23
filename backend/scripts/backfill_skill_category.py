"""存量 Skill 节点 category 一次性回填。

背景：kg_service._upsert_skill_node 仅在 ON CREATE 时写入 category，历史节点
（白名单 yaml 引入分类前建库）缺失或过期。本脚本以 configs/skill_whitelist.yaml
加载的 SKILL_CATEGORY（name → category）为单一事实源，把白名单内技能名的
Skill 节点 category 补齐/纠正；白名单外的节点不碰（保持原值，不写"未分类"）。

用法:
    uv run python scripts/backfill_skill_category.py --dry-run   # 只看将改多少
    uv run python scripts/backfill_skill_category.py             # 实际回填

幂等设计，可重复执行（值已一致的节点不再写）。
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.logging import setup_logging

logger = setup_logging("backfill_skill_category")

from neo4j import GraphDatabase

from app.core.config import settings
from app.services.extraction.dictionary import SKILL_CATEGORY


def _category_counts(session) -> dict[str, int]:
    """当前 Skill.category 分布（含缺失桶 <NULL>）。"""
    rows = session.run(
        "MATCH (s:Skill) "
        "RETURN coalesce(s.category, '<NULL>') AS cat, count(s) AS c "
        "ORDER BY c DESC"
    )
    return {rec["cat"]: rec["c"] for rec in rows}


def run(dry_run: bool) -> int:
    # 空分类（yaml 回退内置集时 category 为空串）不具区分意义，跳过
    mapping = {name: cat for name, cat in SKILL_CATEGORY.items() if cat}
    if not mapping:
        logger.error("✗ SKILL_CATEGORY 为空（skill_whitelist.yaml 加载失败？），终止")
        return 1
    soft = sum(1 for c in mapping.values() if c == "软技能")
    logger.info(
        "白名单映射 %d 条（其中软技能 %d 条）", len(mapping), soft
    )

    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )
    try:
        with driver.session() as session:
            before = _category_counts(session)
            logger.info("回填前分布: %s", before)

            stale = session.run(
                """
                UNWIND $rows AS row
                MATCH (s:Skill {name: row.name})
                WHERE s.category IS NULL OR s.category <> row.category
                RETURN count(s) AS c
                """,
                rows=[{"name": n, "category": c} for n, c in mapping.items()],
            ).single()["c"]

            if stale == 0:
                logger.info("✓ 全部白名单技能节点 category 已一致，无需回填")
                return 0
            if dry_run:
                logger.info("[dry-run] 将更新 %d 个节点，未写库", stale)
                return 0

            summary = session.run(
                """
                UNWIND $rows AS row
                MATCH (s:Skill {name: row.name})
                WHERE s.category IS NULL OR s.category <> row.category
                SET s.category = row.category
                RETURN count(s) AS updated
                """,
                rows=[{"name": n, "category": c} for n, c in mapping.items()],
            ).single()

            after = _category_counts(session)
            logger.info("✓ 已更新 %d 个 Skill 节点", summary["updated"])
            logger.info("回填后分布: %s", after)
            return 0
    finally:
        driver.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="存量 Skill 节点 category 回填")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只统计将变更的节点数，不写库",
    )
    args = parser.parse_args()
    sys.exit(run(args.dry_run))
