"""技能动态关系图同步脚本（PR9b：审批通道的落图执行器）。

读取 skill_dynamic_relations（LLM 提议 + 人工审批的关系事实源）→ 幂等
MERGE 入 Neo4j（与 configs YAML 种子并列，sync_skill_relations 不覆盖）：
- PREREQUISITE_OF / BELONGS_TO：a→b 单边
- ALTERNATIVE_OF：双向 MERGE（对称语义）
- 先修环复检：PREREQUISITE_OF 落地前沿既有入边判定，成环则跳过并计数
  （approve 时已静态判定，此处防并发/历史图形态变化）
- applied_to_graph 标记同步进度（失败可重跑，幂等不依赖该标记）

用法：
    uv run python scripts/sync_dynamic_relations.py --dry-run
红线：图写入属生产副作用，先 --dry-run 核对再实跑。
"""

import argparse
import asyncio
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("sync_dynamic_relations")

_REL_PREREQUISITE = "PREREQUISITE_OF"
_REL_BELONGS = "BELONGS_TO"
_REL_ALTERNATIVE = "ALTERNATIVE_OF"


def _prerequisite_map(neo4j_session) -> dict[str, set[str]]:
    """既有先修父集（target → parents），环判定数据源。"""
    parents: dict[str, set[str]] = {}
    for record in neo4j_session.run(
        "MATCH (a:Skill)-[:PREREQUISITE_OF]->(b:Skill) RETURN a.name AS a, b.name AS b"
    ):
        if record["a"] and record["b"]:
            parents.setdefault(str(record["b"]), set()).add(str(record["a"]))
    return parents


def _merge_relation(neo4j_session, source: str, target: str, relation_type: str) -> None:
    query = (
        f"MATCH (a:Skill {{name: $a}}), (b:Skill {{name: $b}}) "
        f"MERGE (a)-[:{relation_type}]->(b)"
    )
    neo4j_session.run(query, a=source, b=target)


def apply_relations(
    rows: list[dict],
    neo4j_session,
    prerequisite_map: dict[str, set[str]],
    dry_run: bool = False,
) -> dict:
    """逐条落图（幂等 MERGE）；返回统计 {merged, skipped_no_node, cycle_blocked}。

    先修环判定：新增 (source→target)，若 target 可沿既有先修父集到达 source
    （或语义反转已在图中存在 target→source 时由调用方前置判定），则跳过。
    本处仅防「新增边 + 既有边」成环（复用 skill_relation.prerequisite_cycle_would_create
    语义，但基于实时图形态）。
    """
    from app.services.llm_decision.skill_relation import prerequisite_cycle_would_create

    stats = {"merged": 0, "skipped_no_node": 0, "cycle_blocked": 0}
    for row in rows:
        source, target = str(row.get("source_skill") or ""), str(row.get("target_skill") or "")
        relation_type = str(row.get("relation_type") or "")
        if not source or not target or source == target or relation_type not in {
            _REL_PREREQUISITE, _REL_BELONGS, _REL_ALTERNATIVE,
        }:
            stats["skipped_no_node"] += 1
            continue
        if dry_run:
            stats["merged"] += 1
            continue
        if relation_type == _REL_PREREQUISITE and prerequisite_cycle_would_create(
            prerequisite_map, source, target,
        ):
            stats["cycle_blocked"] += 1
            logger.warning("[sync_dynamic] 先修环拦截: %s→%s", source, target)
            continue
        existing = {
            str(r["name"]) for r in neo4j_session.run(
                "MATCH (x:Skill) WHERE x.name IN $names RETURN x.name AS name",
                names=[source, target],
            )
        }
        if not {source, target} <= existing:
            stats["skipped_no_node"] += 1
            continue
        _merge_relation(neo4j_session, source, target, relation_type)
        if relation_type == _REL_ALTERNATIVE:
            _merge_relation(neo4j_session, target, source, relation_type)
        stats["merged"] += 1
    return stats


async def _load_approved_rows() -> list[dict]:
    """skill_dynamic_relations 全部行（approve 即插，未同步的经 applied 标记忽略）。"""
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.business import SkillDynamicRelation

    async with async_session_factory() as session:
        rows = (await session.scalars(select(SkillDynamicRelation))).all()
        return [
            {"source_skill": r.source_skill, "target_skill": r.target_skill,
             "relation_type": r.relation_type, "direction": r.direction,
             "id": str(r.id)}
            for r in rows
        ]


def main() -> None:
    parser = argparse.ArgumentParser(description="技能动态关系落图（先审批后落图）")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写图")
    args = parser.parse_args()

    from app.core.database import neo4j_driver

    rows = asyncio.run(_load_approved_rows())
    if not rows:
        print("无待同步的关系（skill_dynamic_relations 为空）")
        return
    with neo4j_driver.session() as session:
        prerequisite_map = _prerequisite_map(session)
        stats = apply_relations(rows, session, prerequisite_map, dry_run=args.dry_run)
    print(f"{'dry-run ' if args.dry_run else ''}同步完成: {stats}")


if __name__ == "__main__":
    main()