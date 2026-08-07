"""证据关系迁移：MENTIONED_IN → EVIDENCED_BY（设计文档 §5.1 命名对齐）。

历史版本以 `(Skill)-[:MENTIONED_IN]->(Evidence)` 建边，设计文档要求技能由
证据支撑的关系名为 `EVIDENCED_BY`。本脚本将已有 MENTIONED_IN 边重命名为
EVIDENCED_BY（MERGE 目标边幂等），删除旧边，可安全重跑。

用法：
  python scripts/migrate_evidence_relations.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import neo4j_driver


def migrate() -> dict:
    with neo4j_driver.session() as session:
        before = session.run("MATCH ()-[r:MENTIONED_IN]->() RETURN count(r) AS c").single()["c"]
        session.run(
            """
            MATCH (s:Skill)-[r:MENTIONED_IN]->(e:Evidence)
            MERGE (s)-[nr:EVIDENCED_BY]->(e)
            DELETE r
            """
        )
        after = session.run("MATCH ()-[r:MENTIONED_IN]->() RETURN count(r) AS c").single()["c"]
        migrated = session.run("MATCH ()-[r:EVIDENCED_BY]->() RETURN count(r) AS c").single()["c"]
    return {"renamed_from_old": before, "old_remaining": after, "evidence_by_count": migrated}


if __name__ == "__main__":
    result = migrate()
    print(f"迁移完成: 旧 MENTIONED_IN {result['renamed_from_old']} 条 → "
          f"剩余 {result['old_remaining']} 条，EVIDENCED_BY 现 {result['evidence_by_count']} 条")
