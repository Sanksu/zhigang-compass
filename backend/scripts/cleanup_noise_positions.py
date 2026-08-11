"""图谱岗位清理：删除碎片/业务词空岗节点（岗位评估报告 P0-A 存量清理）。

清理口径（与归一化防复发逻辑一致）：
- 仅清理"零技能空岗"（无 REQUIRES 出边）且归一化结果命中 _POSITION_STOPWORDS
  的 Position 节点（如 专利/传播/跟单员/量化/中训练 等 LLM 误抽业务词/碎片）。
  英文未翻译岗（AI Infra Engineer 等）与真实细分岗（保险分析师等）归一化非空，
  不在清理范围，由后续 P0-B/归并方案处理。

注意：删除不可逆，运行前建议 --dry-run 确认。

用法：
    python scripts/cleanup_noise_positions.py
    python scripts/cleanup_noise_positions.py --dry-run   # 仅报告，不删除
"""

import argparse
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("cleanup_noise_positions")

from app.core.database import neo4j_driver
from app.services.extraction.dictionary import normalize_position_name


def _has_skill_requires(session, pos_id: str) -> bool:
    """零技能空岗口径：无 REQUIRES->Skill 出边（REQUIRES->Education 等学历边不算）。"""
    return session.run(
        "MATCH (p:Position {id: $id})-[:REQUIRES]->(:Skill) RETURN count(*) AS c",
        id=pos_id,
    ).single()["c"] > 0


def collect_noise_positions(session) -> list[dict]:
    """零技能空岗中，归一化后命中停用词（返回空串）的碎片岗位。"""
    rows = session.run("MATCH (p:Position) RETURN p.id AS id, p.name AS name").data()
    noise = []
    for r in rows:
        if _has_skill_requires(session, r["id"]):
            continue
        if normalize_position_name(r["name"]) == "":
            noise.append(r)
    return noise


def main() -> None:
    parser = argparse.ArgumentParser(description="删除碎片/业务词空岗 Position 节点")
    parser.add_argument("--dry-run", action="store_true", help="仅报告，不删除")
    args = parser.parse_args()

    with neo4j_driver.session() as session:
        noise = collect_noise_positions(session)
        logger.info("命中碎片/业务词空岗 %s 个:", len(noise))
        for r in sorted(noise, key=lambda x: x["name"]):
            logger.info("  %s", r["name"])

        if not args.dry_run and noise:
            ids = [r["id"] for r in noise]
            with session.begin_transaction() as tx:
                tx.run(
                    "MATCH (p:Position) WHERE p.id IN $ids DETACH DELETE p",
                    ids=ids,
                )
            logger.info("已删除 %s 个碎片空岗节点", len(noise))
        elif args.dry_run:
            logger.info("[dry-run] 未删除（共 %s 个待清理）", len(noise))

        total = session.run("MATCH (p:Position) RETURN count(p) AS c").single()["c"]
        logger.info("当前 Position 节点总数: %s", total)
        neo4j_driver.close()


if __name__ == "__main__":
    main()
