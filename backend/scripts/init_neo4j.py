"""Neo4j 图谱初始化：加载 schema.cypher，创建约束/索引/全文索引/Counter 节点。

用法:
    uv run python scripts/init_neo4j.py

幂等设计，可重复执行。
"""

import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.logging import setup_logging

logger = setup_logging("init_neo4j")

from neo4j import GraphDatabase
from app.core.config import settings

# schema.cypher 路径（相对于项目根）
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "app" / "services" / "kg" / "schema.cypher"


def _parse_statements(text: str) -> list[str]:
    """按 ; 分割 Cypher 语句，过滤空行与注释。"""
    stmts = []
    for block in text.split(";"):
        block = block.strip()
        if not block:
            continue
        # 去掉单行注释
        lines = [ln for ln in block.split("\n") if not ln.strip().startswith("//")]
        cleaned = " ".join(lines).strip()
        if cleaned:
            stmts.append(cleaned + ";")
    return stmts


def run() -> int:
    """执行 schema 初始化，返回退出码（0=全部成功，1=存在失败语句）。"""
    if not SCHEMA_PATH.exists():
        logger.error("✗ schema.cypher 未找到: %s", SCHEMA_PATH)
        return 1

    cypher_text = SCHEMA_PATH.read_text(encoding="utf-8")
    statements = _parse_statements(cypher_text)

    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )

    total = len(statements)
    ok = 0
    errors = []

    try:
        logger.info("加载 schema.cypher — 共 %s 条语句", total)
        with driver.session() as session:
            for i, cql in enumerate(statements, 1):
                try:
                    session.run(cql)
                    ok += 1
                    label = cql.split()[1] if cql.split()[0].upper() in (
                        "CREATE", "MERGE") else cql[:40]
                    logger.info("  [%2d/%d] ✓ %s...", i, total, label)
                except Exception as e:
                    errors.append((i, cql[:80], str(e)))
                    logger.error("  [%2d/%d] ✗ %s… → %s", i, total, cql[:60], e)

        logger.info(f"\n完成: {ok}/{total} 成功, {len(errors)} 失败.")
        if errors:
            for idx, snippet, reason in errors:
                logger.error(f"  [{idx}] {snippet} → {reason}")

    finally:
        driver.close()

    # 存在失败语句返回非 0：部署脚本/CI 可据此判定初始化失败
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(run())
