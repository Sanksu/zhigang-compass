"""为图谱存量岗位回填 BELONGS_TO_OCCUPATION 关系（设计文档 §5.1）。

背景：Occupation 对齐在 import_jd 入图时生效（新岗位），存量岗位无
occupation_code / BELONGS_TO_OCCUPATION 边。本脚本全量回填：
`MATCH (p:Position) WHERE p.occupation_code IS NULL`，对齐规则与
occupation_align.py 一致（规则优先 + SBERT 语义兜底）。

前置步骤：按 PG occupations 权威源同步 Neo4j Occupation 节点（MERGE by code
幂等）。回填会 MERGE 边指向的 Occupation 节点，若 Neo4j 节点缺失（依赖
import_occupations 手动同步，可能未跑/失败）会建出只有 code 的空节点，
故先补齐节点属性（occupation_align 数据源已改 PG，同步口径一致）。

用法：
    python scripts/align_positions.py            # 全量回填未对齐岗位
    python scripts/align_positions.py --dry-run  # 仅统计命中，不写入
"""

import argparse
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("align_positions")

from app.core.database import neo4j_driver
from app.services.kg.occupation_align import OccupationAligner


def _sync_occupation_nodes() -> int:
    """把 PG occupations 全量同步为 Neo4j Occupation 节点（MERGE by code 幂等）。"""
    from sqlalchemy import create_engine, text

    from app.core.config import settings

    dsn = settings.postgres_dsn.replace(
        "postgresql+asyncpg://", "postgresql+psycopg2://"
    )
    engine = create_engine(dsn, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT code, name, category, definition, aliases FROM occupations")
            ).mappings().all()
    finally:
        engine.dispose()
    if not rows:
        return 0
    payload = [dict(r) for r in rows]
    with neo4j_driver.session() as ns:
        ns.run(
            "UNWIND $rows AS r "
            "MERGE (o:Occupation {code: r.code}) "
            "SET o.name = r.name, o.category = r.category, "
            "o.definition = r.definition, o.aliases = r.aliases",
            rows=payload,
        )
    return len(payload)


def main(dry_run: bool) -> None:
    try:
        n = _sync_occupation_nodes()
        logger.info("Occupation 节点就绪: %s 个", n)
    except Exception as e:
        logger.warning("Occupation 节点同步失败（对齐继续，回填边可能指向空节点）: %s", e)

    with neo4j_driver.session() as session:
        rows = session.run(
            "MATCH (p:Position) WHERE p.occupation_code IS NULL "
            "RETURN p.id AS id, p.name AS name"
        ).data()
    positions = [(r["id"], r["name"]) for r in rows]
    logger.info("待回填岗位 %s 个", len(positions))

    aligner = OccupationAligner.get()
    matched = 0
    with neo4j_driver.session() as session:
        for pid, name in positions:
            occ = aligner.align(name)
            if occ is None:
                continue
            matched += 1
            if dry_run:
                continue
            code, conf = occ
            session.run(
                "MATCH (p:Position {id: $id}) "
                "SET p.occupation_code = $code "
                "MERGE (p)-[:BELONGS_TO_OCCUPATION {confidence: $conf}]->(o:Occupation {code: $code})",
                id=pid,
                code=code,
                conf=conf,
            )
    suffix = "[dry-run] " if dry_run else ""
    logger.info(f"{suffix}对齐命中 {matched}/{len(positions)} 个，其余未命中不入边")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="存量岗位 Occupation 回填")
    parser.add_argument("--dry-run", action="store_true", help="仅统计不写入")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
