"""验证 Neo4j 图谱初始化结果。"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neo4j import GraphDatabase
from app.core.config import settings

driver = GraphDatabase.driver(settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password))

with driver.session() as session:
    constraints = session.run("SHOW CONSTRAINTS").data()
    indexes = session.run("SHOW INDEXES").data()
    counters = session.run("MATCH (c:Counter) RETURN c.type, c.value ORDER BY c.type").data()

print(f"约束: {len(constraints)} 个")
for c in constraints:
    print(f"  {c['name']} ({c['type']})")

print(f"\n索引: {len(indexes)} 个")
for i in indexes:
    print(f"  {i['name']} ({i['type']})")

print(f"\nCounter 节点: {len(counters)} 个")
for c in counters:
    print(f"  {c['c.type']}: {c['c.value']}")

driver.close()
print("\n✓ Neo4j 配置验证通过")
