"""为图谱存量岗位回填 BELONGS_TO_OCCUPATION 关系（设计文档 §5.1）。

背景：Occupation 对齐在 import_jd 入图时生效（新岗位），存量岗位无
occupation_code / BELONGS_TO_OCCUPATION 边。本脚本全量回填：
`MATCH (p:Position) WHERE p.occupation_code IS NULL`，对齐规则与
occupation_align.py 一致（规则优先 + SBERT 语义兜底）。

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


def main(dry_run: bool) -> None:
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
