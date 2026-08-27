"""技能关系 LLM 提议编排（ETL/脚本共用的核心实现）。

首窗口只生成 proposal（llm_decision_records status=proposal，risk_tier=R2），
审核通过前不写入图谱。候选来源=同一岗位 REQUIRES 技能对共现（top-N）；
硬门=节点存在/无自指/方向匹配 + 运行侧先修环判定（环候选直接 blocked）。

本模块为 app 包内实现（worker 与 scripts 共用）：全 async（落库直接
await persist_record；Neo4j 读走 to_thread），scripts 薄壳以 asyncio.run
提供同步入口。

红线：prompt 与方向语义属算法核心（services/llm_decision/skill_relation.py），
变更须张恺天 review。
"""

import asyncio
import hashlib
from collections import Counter
from datetime import datetime, timedelta, timezone

from app.core.logging import setup_logging

logger = setup_logging("propose_skill_relations")

MIN_COOCCUR = 2  # 共现次数下限（过滤一次共现的偶发对）
DEFAULT_LIMIT = 40  # 每日关系候选上限（控制 LLM 成本）


def fetch_relation_inputs(driver) -> tuple[dict, set[str]]:
    """图谱输入（一次读取）：cooccurrence 候选表 + 已知技能名集合。

    cooccurrence: {(源, 目标): [{position, count}]}——同岗 REQUIRES 集合内
    两两配对（无向键 a<b），evidence 记录具体岗位与出现次数（证据可追溯）。
    """
    cooccurrence: dict[tuple[str, str], list[dict]] = {}
    positions_of: dict[tuple[str, str], Counter] = {}
    with driver.session() as session:
        for record in session.run(
            "MATCH (p:Position)-[r:REQUIRES]->(s:Skill) "
            "WITH p.name AS position, collect(s.name) AS skills "
            "RETURN position, skills"
        ):
            skills = sorted({s for s in record["skills"] if s})
            for i in range(len(skills)):
                for j in range(i + 1, len(skills)):
                    pair = (skills[i], skills[j])
                    positions_of.setdefault(pair, Counter())[record["position"]] += 1
        known = {
            str(r["name"]) for r in session.run("MATCH (s:Skill) RETURN s.name AS name") if r["name"]
        }
    for pair, cnt in positions_of.items():
        cooccurrence[pair] = [
            {"position": p, "count": c}
            for p, c in sorted(cnt.items(), key=lambda x: -x[1])
        ]
    return cooccurrence, known


def select_candidates(
    cooccurrence: dict[tuple[str, str], list[dict]],
    limit: int,
    min_cooccur: int = MIN_COOCCUR,
) -> list[dict]:
    """共现对 → 候选（按总共现次数降序，过滤低于下限者）。"""
    scored = sorted(
        (
            (sum(c["count"] for c in evidence), (source, target), evidence)
            for (source, target), evidence in cooccurrence.items()
        ),
        key=lambda x: -x[0],
    )
    return [
        {"source": source, "target": target, "total": total, "evidence": evidence}
        for total, (source, target), evidence in scored
        if total >= min_cooccur
    ][:limit]


def _relation_direction_map(driver) -> dict[str, set[str]]:
    """既有 PREREQUISITE_OF 先修父集（target → parents），供运行侧环判定。"""
    parents: dict[str, set[str]] = {}
    with driver.session() as session:
        for record in session.run(
            "MATCH (a:Skill)-[:PREREQUISITE_OF]->(b:Skill) RETURN a.name AS a, b.name AS b"
        ):
            if record["a"] and record["b"]:
                parents.setdefault(str(record["b"]), set()).add(str(record["a"]))
    return parents


def _provider_of(llm) -> tuple[str, str]:
    try:
        primary = (llm._providers or [{}])[0]
        return str(primary.get("name") or ""), str(primary.get("model") or "")
    except Exception:
        return "", ""


async def propose(limit: int = DEFAULT_LIMIT) -> dict:
    """执行一轮关系提议（async，ETL worker 直调）；返回摘要。"""
    from app.core.database import neo4j_driver
    from app.services.extraction.llm_provider import LLMConfigurationError, LLMProviderChain
    from app.services.llm_decision import (
        DOMAIN_SKILL_RELATION,
        STATUS_BLOCKED,
        STATUS_PROPOSAL,
        build_record,
        persist_record,
    )
    from app.services.llm_decision.skill_relation import (
        REL_PREREQUISITE,
        decide_skill_relation,
        prerequisite_cycle_would_create,
        skill_relation_gate,
    )

    cooccurrence, known = await asyncio.to_thread(fetch_relation_inputs, neo4j_driver)
    candidates = select_candidates(cooccurrence, limit)
    if not candidates:
        return {"status": "ok", "candidates": 0, "proposed": 0, "blocked": 0, "llm_failed": 0}

    try:
        llm = LLMProviderChain()
    except LLMConfigurationError:
        return {"status": "skipped", "reason": "LLM 未配置"}

    provider, model = _provider_of(llm)
    prerequisite_map = await asyncio.to_thread(_relation_direction_map, neo4j_driver)
    run_date = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    proposed = blocked = llm_failed = 0
    for cand in candidates:
        # 同步 LLM client → to_thread（第六轮审查 P1-2，防阻塞 ARQ 事件循环）
        decision = await asyncio.to_thread(
            decide_skill_relation, cand["source"], cand["target"], cand["evidence"], llm,
        )
        if decision is None:
            llm_failed += 1
            continue
        gate_ok, gate_reason = skill_relation_gate(decision, cand["source"], cand["target"], known)
        if gate_ok and decision.relation == REL_PREREQUISITE:
            if prerequisite_cycle_would_create(prerequisite_map, cand["source"], cand["target"]):
                gate_ok, gate_reason = False, "先修环判定拦截（新增边会成环）"
        record = build_record(
            domain=DOMAIN_SKILL_RELATION,
            entity_type="skill_relation",
            entity_id=f"{cand['source']}->{cand['target']}",
            run_id=f"relation_propose:{run_date}",
            input_hash=hashlib.sha256(
                f"{cand['source']}\n{cand['target']}".encode("utf-8")
            ).hexdigest(),
            evidence_refs=cand["evidence"],
            provider=provider, model=model,
            structured_output={
                "relation": decision.relation, "direction": decision.direction,
                "reason": decision.reason, "total_cooccur": cand["total"],
            },
            confidence=decision.confidence,
            gate_result="blocked" if not gate_ok else "pass",
            risk_tier="R2",
            status=STATUS_BLOCKED if not gate_ok else STATUS_PROPOSAL,
        )
        await persist_record(record)
        if gate_ok:
            proposed += 1
            logger.info("[propose_relations] %s → %s: %s(%s)", cand["source"], cand["target"],
                        decision.relation, decision.direction)
        else:
            blocked += 1
            logger.info("[propose_relations] %s → %s 拦截: %s", cand["source"], cand["target"], gate_reason)

    summary = {
        "status": "ok", "run_date": run_date,
        "candidates": len(candidates), "proposed": proposed,
        "blocked": blocked, "llm_failed": llm_failed,
        "provider": provider, "model": model,
    }
    logger.info("[propose_relations] 候选 %d → 提议 %d / 拦截 %d / 失败 %d",
                len(candidates), proposed, blocked, llm_failed)
    return summary
