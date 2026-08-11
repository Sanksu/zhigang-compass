"""技能关系建边同步（设计文档 §5.1 九类关系补齐）。

将字典数据落地为 Neo4j 边（幂等 MERGE，可安全重跑）：
- PREREQUISITE_OF：先修字典 → 边
- BELONGS_TO / ALTERNATIVE_OF：技能关系字典 → 边

用法：
  python scripts/sync_skill_relations.py            # 全量执行
  python scripts/sync_skill_relations.py --dry-run  # 只统计不写图谱
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.logging import setup_logging

logger = setup_logging("sync_skill_relations")

from app.core.database import neo4j_driver
from app.services.kg.skill_relations import sync_skill_relations


def main(dry_run: bool = False) -> None:
    with neo4j_driver.session() as session:
        stats = sync_skill_relations(session, dry_run=dry_run)
    suffix = "（dry-run，未写图谱）" if dry_run else ""
    logger.info(
        f"技能关系同步{suffix}: PREREQUISITE_OF={stats['prerequisite']} 条, "
        f"BELONGS_TO={stats['belongs_to']} 条, "
        f"ALTERNATIVE_OF={stats['alternative_of']} 条, "
        f"跳过(技能不在图谱)={stats['skipped']} 条"
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="技能关系建边同步")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写图谱")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
