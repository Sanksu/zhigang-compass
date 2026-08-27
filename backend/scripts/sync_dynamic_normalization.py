"""名称归一审批落图同步脚本（PR3 c：approve 通道的图变更执行器）。

读取 name_normalization_requests（LLM 提议 + 人工审批的 rename/merge 事实源）→
幂等应用到 Neo4j（Position / Skill 节点）。审批仅在 API 端点写 PG（对齐
skill_relation 的「approve 写 PG、独立 sync 脚本写图」），图写入由本脚本完成。

语义（按图形态自纠正，dry-run 也判形态）：
- merge：目标规范名节点已存在 → 把 source 节点的入/出边重连到 target，合并
  freq，删除 source（幂等：无 source 节点或目标缺失则跳过）。
- rename：目标规范名节点不存在 → SET source.name=target（幂等：已改名则无
  source 节点，跳过）。

两种实体类型（Skill / Position）复用同一套边迁移逻辑：图中入边/出边按实体
类型显式枚举，避免误迁移。applied_to_graph 标记同步进度（幂等不依赖该标记，
失败可重跑）。

用法：
    uv run python scripts/sync_dynamic_normalization.py --dry-run
红线：图写入属生产副作用，先 --dry-run 核对再实跑。
"""

import argparse
import asyncio
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("sync_dynamic_normalization")

# 每种实体类型的入/出边（图谱 schema：岗位→技能 REQUIRES；技能→技能
# PREREQUISITE_OF/BELONGS_TO/ALTERNATIVE_OF；技能→课程 LEARNABLE_VIA；
# 岗位→证据 HAS_EVIDENCE）。迁移出边（source 为起点）与入边（source 为终点）。
_ENTITY_EDGES: dict[str, dict[str, list[str]]] = {
    "skill": {
        "out": ["PREREQUISITE_OF", "BELONGS_TO", "ALTERNATIVE_OF", "LEARNABLE_VIA"],
        "in": ["REQUIRES"],
    },
    "position": {
        "out": [],
        "in": [],
    },
}
# 岗位节点除 REQUIRES（上表 skill.in 已在 source=skill 时迁移）外，位置为源的
# HAS_EVIDENCE 入边（course/evidence 指向岗位）与出边不生产图，统一按边迁移。
# 实际岗位合并重用 position_duplicate_cleanup._merge_graph 的 REQUIRES/HAS_EVIDENCE
# 语义（source 为岗位时迁移其出边）。为覆盖岗位归并，这里给 position 显式补边。
_ENTITY_EDGES["position"] = {
    "out": ["REQUIRES", "HAS_EVIDENCE"],
    "in": [],
}


def _node_label(entity_type: str) -> str:
    if entity_type == "position":
        return "Position"
    if entity_type == "skill":
        return "Skill"
    raise ValueError(f"未知实体类型 {entity_type!r}")


def _exists(session, entity_type: str, name: str) -> bool:
    label = _node_label(entity_type)
    row = session.run(
        f"MATCH (n:{label} {{name: $name}}) RETURN count(n) AS c",
        name=name,
    ).single()
    return bool(row and row["c"])


def _merge_node(session, entity_type: str, source: str, target: str) -> None:
    """把 source 节点的入/出边重连到 target，合并 freq，删除 source（幂等）。

    边迁移用 MERGE ON CREATE（重复执行安全）；source 不存在或目标缺失则整体跳过
    （由 apply 预判）。freq 用 SET 累加（重复执行会重复累加，但 apply 侧已用
    applied_to_graph 标记幂等，且 DETACH DELETE 后 source 消失即不再累加）。
    """
    label = _node_label(entity_type)
    for kind in _ENTITY_EDGES[entity_type]["out"]:
        session.run(
            f"""
            MATCH (d:{label} {{name: $source}})-[r:{kind}]->(t)
            MATCH (k:{label} {{name: $target}})
            MERGE (k)-[r2:{kind}]->(t)
            ON CREATE SET r2 = properties(r)
            DELETE r
            """,
            source=source, target=target,
        )
    for kind in _ENTITY_EDGES[entity_type]["in"]:
        session.run(
            f"""
            MATCH (d:{label} {{name: $source}})<-[r:{kind}]-(s)
            MATCH (k:{label} {{name: $target}})
            MERGE (s)-[r2:{kind}]->(k)
            ON CREATE SET r2 = properties(r)
            DELETE r
            """,
            source=source, target=target,
        )
    session.run(
        f"""
        MATCH (d:{label} {{name: $source}}), (k:{label} {{name: $target}})
        SET k.freq = coalesce(k.freq, 0) + coalesce(d.freq, 0)
        DETACH DELETE d
        """,
        source=source, target=target,
    )


def _rename_node(session, entity_type: str, source: str, target: str) -> None:
    label = _node_label(entity_type)
    session.run(
        f"MATCH (n:{label} {{name: $source}}) SET n.name = $target",
        source=source, target=target,
    )


def apply_normalizations(rows: list[dict], neo4j_session, dry_run: bool = False) -> dict:
    """逐条落图（幂等）；返回统计 {renamed, merged, skipped_no_source, skipped_no_target}。"""
    stats = {"renamed": 0, "merged": 0, "skipped_no_source": 0, "skipped_no_target": 0}
    for row in rows:
        entity_type = str(row.get("entity_type") or "")
        action = str(row.get("action") or "")  # 审计意图（rename/merge），实际操作以图形态为准
        source = str(row.get("source_name") or "").strip()
        target = str(row.get("target_name") or "").strip()
        # entity_type 合法即接受；action 仅记录，不驱动判定（见模块 docstring）。
        _ = action
        if entity_type not in _ENTITY_EDGES or not source or not target or source == target:
            stats["skipped_no_source"] += 1
            continue
        if dry_run:
            if _exists(neo4j_session, entity_type, target):
                stats["merged"] += 1
            elif _exists(neo4j_session, entity_type, source):
                stats["renamed"] += 1
            else:
                stats["skipped_no_source"] += 1
            continue

        source_exists = _exists(neo4j_session, entity_type, source)
        target_exists = _exists(neo4j_session, entity_type, target)
        if not source_exists:
            # 已改名/已删除（幂等重跑）→ 视为完成，跳过
            stats["skipped_no_source"] += 1
            continue
        if target_exists:
            _merge_node(neo4j_session, entity_type, source, target)
            stats["merged"] += 1
            logger.info("[sync_norm] %s MERGE %s → %s", entity_type, source, target)
        else:
            _rename_node(neo4j_session, entity_type, source, target)
            stats["renamed"] += 1
            logger.info("[sync_norm] %s RENAME %s → %s", entity_type, source, target)
    return stats


async def _mark_applied(ids: list[str], proposal_ids: list[str] | None = None) -> None:
    """把已落图行置 applied_to_graph=True + 源决策 effects_applied=True。

    仅标记「有变更意图且已成功（或幂等跳过）」的行；source 节点缺失视为已
    完成（改名/删除后重跑），故一并标记，失败可重跑。effects_applied 回写
    恢复 #570 对账语义（第六轮审查：approve 改置 False 待落图）。
    """
    if not ids:
        return
    from sqlalchemy import update

    from app.core.database import async_session_factory
    from app.models.business import LLMDecisionRecord, NameNormalizationRequest

    async with async_session_factory() as session:
        await session.execute(
            update(NameNormalizationRequest)
            .where(NameNormalizationRequest.id.in_(ids))
            .values(applied_to_graph=True)
        )
        if proposal_ids:
            await session.execute(
                update(LLMDecisionRecord)
                .where(LLMDecisionRecord.id.in_(proposal_ids))
                .values(effects_applied=True)
            )
        await session.commit()


async def _load_rows() -> tuple[list[dict], list[str]]:
    """name_normalization_requests 全量行（approve 即插；applied 标记同步进度）。"""
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.business import NameNormalizationRequest

    async with async_session_factory() as session:
        rows = (await session.scalars(select(NameNormalizationRequest))).all()
        if not rows:
            return [], []
        payload = [
            {"entity_type": r.entity_type, "action": r.action,
             "source_name": r.source_name, "target_name": r.target_name,
             "id": str(r.id), "proposal_id": r.proposal_id}
            for r in rows
        ]
        # 有变更意图（源/目标非空且不同）且已被 sync 处理的行 id，供 applied 标记
        changed = [p for p in payload
                   if (p["source_name"] or "") and (p["target_name"] or "")
                   and p["source_name"] != p["target_name"]]
        applied_ids = [p["id"] for p in changed]
        proposal_ids = [p["proposal_id"] for p in changed if p.get("proposal_id")]
        return payload, applied_ids, proposal_ids


async def _run_sync(dry_run: bool) -> dict:
    """单事件循环同步：_load_rows + apply + _mark_applied（修复 08-26 发现的三处
    重复 asyncio.run windows asyncpg 跨 loop bug——applied_to_graph 标记失效）。"""
    payload, applied_ids, proposal_ids = await _load_rows()
    if not payload:
        return {"payload": 0}
    from app.core.database import neo4j_driver

    with neo4j_driver.session() as session:
        stats = apply_normalizations(payload, session, dry_run=dry_run)
    if not dry_run and applied_ids:
        await _mark_applied(applied_ids, proposal_ids)
    return {"payload": len(payload), **stats}


def main() -> None:
    parser = argparse.ArgumentParser(description="名称归一审批落图（先审批后落图）")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写图")
    args = parser.parse_args()
    # 单 asyncio.run 驱动全流程（避免 Windows 重复 asyncio.run 跨 loop）——修复 _mark_applied
    result = asyncio.run(_run_sync(dry_run=args.dry_run))
    if result.get("payload") == 0:
        print("无待同步的名称归一（name_normalization_requests 为空）")
        return
    stats = {k: v for k, v in result.items() if k != "payload"}
    print(f"{'dry-run ' if args.dry_run else ''}同步完成: {stats}")


if __name__ == "__main__":
    main()
