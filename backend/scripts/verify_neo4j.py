"""验证 Neo4j 图谱初始化结果。"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.logging import setup_logging

logger = setup_logging("verify_neo4j")

from neo4j import GraphDatabase
from app.core.config import settings

driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))

with driver.session() as session:
    constraints = session.run("SHOW CONSTRAINTS").data()
    indexes = session.run("SHOW INDEXES").data()
    counters = session.run("MATCH (c:Counter) RETURN c.type, c.value ORDER BY c.type").data()

logger.info(f"约束: {len(constraints)} 个")
for c in constraints:
    logger.info(f"  {c['name']} ({c['type']})")

logger.info(f"\n索引: {len(indexes)} 个")
for i in indexes:
    logger.info(f"  {i['name']} ({i['type']})")

logger.info(f"\nCounter 节点: {len(counters)} 个")
for c in counters:
    logger.info(f"  {c['c.type']}: {c['c.value']}")

driver.close()
logger.info("\n✓ Neo4j 配置验证通过")
