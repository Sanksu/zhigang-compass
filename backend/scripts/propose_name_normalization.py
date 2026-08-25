"""名称归一 LLM 提议脚本（PR3 c：JD/技能输入 → LLM 归一建议 → proposal 决策记录）。

首窗口只生成 proposal（llm_decision_records status=proposal，risk_tier=R2），
审核通过前不写入图谱（rename/merge 属 R2 高风险图变异）。区别于 shadow 影子
（status=shadow，只落档不生效）：本脚本把「规则无法稳定裁决」的候选提为人工
审核池，由 admin 在决策页 approve 后，scripts/sync_dynamic_normalization.py
幂等应用到 Neo4j。

- 岗位名：最近抽取 JD（title + 抽取技能 + 来源 + 图谱候选岗位名，
  PositionCandidateRecaller 语义召回 top-K）
- 技能名：图谱未归一化技能（normalized_name 缺失，低引用优先）

用法：
    uv run python scripts/propose_name_normalization.py --limit 40 [--domain skill|position]

红线：prompt 与硬门属算法核心（services/llm_decision/position_name.py /
skill_normalize.py），变更须张恺天 review。本脚本仅编排，不改判定逻辑。
"""

import argparse
import asyncio
import hashlib
import sys
from datetime import datetime, timedelta, timezone
from json import dumps
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("propose_name_normalization")

DEFAULT_LIMIT = 40  # 每日归一候选上限（控制 LLM 成本）
_CST = timezone(timedelta(hours=8))
# 名称归一是 R2 高风险图变异（rename/merge），必经人工 approve，绝不 auto-apply。
RISK_TIER = "R2"


def _input_hash(kind: str, *parts: str) -> str:
    return hashlib.sha256(
        dumps({"kind": kind, "parts": list(parts)}, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _skills_from_extraction(extraction: dict) -> list[str]:
    skills: list[str] = []
    for s in (extraction.get("skills") or []):
        if isinstance(s, dict) and s.get("name"):
            skills.append(str(s["name"]))
    for req in (extraction.get("requirements") or []):
        if isinstance(req, dict) and req.get("skill_name"):
            skills.append(str(req["skill_name"]))
    return skills


def _existing_positions(driver) -> list[str]:
    """图谱现有候选岗位名（每次运行取一次，供 prompt 证据与 hard gate）。"""
    with driver.session() as session:
        return [
            str(r["name"]) for r in session.run(
                "MATCH (p:Position) RETURN p.name AS name ORDER BY p.freq DESC LIMIT 300"
            ) if r["name"]
        ]


def _fetch_unormalized_skills(driver, limit: int) -> list[dict]:
    """图谱未归一化技能（normalized_name 缺失，新入图优先）。"""
    query = (
        "MATCH (s:Skill) WHERE s.normalized_name IS NULL "
        "RETURN s.name AS name, s.first_seen AS first_seen "
        "ORDER BY s.first_seen DESC LIMIT $limit"
    )
    with driver.session() as session:
        return [dict(record) for record in session.run(query, limit=limit)]


def _recent_jd_rows(limit: int) -> list:
    """最近抽取 JD（快照含 extraction），供岗位名归一候选。

    独立 asyncio.run 拉取（快照已在 select 时加载，会话关闭后列属性仍可用）；
    与逐条 persist_record 的 asyncio.run 分离，避免共享事件循环/会话。
    """
    import asyncio

    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.raw import JDRaw

    async def _fetch() -> list:
        async with async_session_factory() as session:
            rows = list((await session.scalars(
                select(JDRaw)
                .where(JDRaw.snapshot["extraction"].astext.is_not(None))
                .order_by(JDRaw.updated_at.desc())
                .limit(limit)
            )).all())
            # 会话关闭前把列属性拷出（snapshot/source/source_url），
            # 避免离开会话后访问 detached ORM 属性（对齐 shadow worker 语义）。
            return [
                {"snapshot": r.snapshot or {}, "source": r.source or "",
                 "source_url": r.source_url or ""}
                for r in rows
            ]

    return asyncio.run(_fetch())


def _provider_of(llm) -> tuple[str, str]:
    try:
        primary = (llm._providers or [{}])[0]
        return str(primary.get("name") or ""), str(primary.get("model") or "")
    except Exception:
        return "", ""


def propose_position(
    llm, recaller, provider: str, model: str, run_date: str, limit: int,
) -> dict:
    """岗位名归一提议一轮（同步编排；落库经独立 asyncio.run(persist_record)）。

    返回 summary（对齐 shadow worker 语义）。gate-blocked / LLM-failed 计数进内。
    """
    from app.services.llm_decision import (
        DOMAIN_POSITION_NORMALIZE,
        STATUS_BLOCKED,
        STATUS_PROPOSAL,
        build_record,
        persist_record,
    )
    from app.services.llm_decision.position_name import (
        decide_position_name,
        position_name_gate,
    )
    from app.services.extraction.position_normalization import (
        normalized_position_from_snapshot,
    )

    summary = {
        "candidates": 0, "proposed": 0, "blocked": 0, "llm_failed": 0,
        "recall_mode": recaller.mode,
    }
    if recaller.mode != "embedding":
        logger.warning("[propose_norm] 岗位候选召回降级 %s（SBERT 不可用）", recaller.mode)

    rows = _recent_jd_rows(limit)
    for row in rows:
        snap = row["snapshot"] or {}
        extraction = snap.get("extraction") or {}
        title = str(snap.get("title") or "").strip()
        if not title:
            continue
        # entity_id 必须是图谱 Position 节点名（=归一化岗位名），sync 侧按
        # MATCH (n:Position {name: $source}) 匹配；若用原始 JD 标题则永不命中、
        # 归并/改名静默 no-op（08-25 修复）。原始 title 仅用于 LLM 决策输入。
        position_name = normalized_position_from_snapshot(snap) or title
        summary["candidates"] += 1
        skills = _skills_from_extraction(extraction)
        candidates = recaller.recall(title)
        decision = decide_position_name(title, skills, str(row["source"] or ""), candidates, llm)
        if decision is None:
            summary["llm_failed"] += 1
            continue
        gate_ok, gate_reason = position_name_gate(decision, title, candidates)
        record = build_record(
            domain=DOMAIN_POSITION_NORMALIZE,
            entity_type="position", entity_id=position_name, run_id=f"norm_propose:{run_date}",
            input_hash=_input_hash("position", position_name),
            evidence_refs=[{"source": row["source"], "source_url": row["source_url"]}],
            provider=provider, model=model,
            structured_output=decision.model_dump(),
            confidence=decision.confidence,
            gate_result="blocked" if not gate_ok else "pass",
            risk_tier=RISK_TIER,
            status=STATUS_BLOCKED if not gate_ok else STATUS_PROPOSAL,
        )
        asyncio.run(persist_record(record))
        if gate_ok:
            summary["proposed"] += 1
            logger.info("[propose_norm] 岗位 %s → %s（proposal）", title, decision.canonical_name)
        else:
            summary["blocked"] += 1
            logger.info("[propose_norm] 岗位 %s 拦截: %s", title, gate_reason)
    return summary


def propose_skill(
    llm, provider: str, model: str, run_date: str, limit: int,
) -> dict:
    """技能名归一提议一轮（同步编排；落库经独立 asyncio.run(persist_record)）。"""
    from app.core.database import neo4j_driver
    from app.services.llm_decision import (
        DOMAIN_SKILL_NORMALIZE,
        STATUS_BLOCKED,
        STATUS_PROPOSAL,
        build_record,
        persist_record,
    )
    from app.services.llm_decision.skill_normalize import (
        decide_skill_normalize,
        skill_normalize_gate,
    )

    summary = {"candidates": 0, "proposed": 0, "blocked": 0, "llm_failed": 0}
    rows = _fetch_unormalized_skills(neo4j_driver, limit)
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        summary["candidates"] += 1
        decision = decide_skill_normalize(name, llm)
        if decision is None:
            summary["llm_failed"] += 1
            continue
        gate_ok, gate_reason = skill_normalize_gate(decision, name)
        record = build_record(
            domain=DOMAIN_SKILL_NORMALIZE,
            entity_type="skill", entity_id=name, run_id=f"norm_propose:{run_date}",
            input_hash=_input_hash("skill", name),
            evidence_refs=[{"kind": "unormalized_skill", "name": name}],
            provider=provider, model=model,
            structured_output=decision.model_dump(),
            confidence=decision.confidence,
            gate_result="blocked" if not gate_ok else "pass",
            risk_tier=RISK_TIER,
            status=STATUS_BLOCKED if not gate_ok else STATUS_PROPOSAL,
        )
        asyncio.run(persist_record(record))
        if gate_ok:
            summary["proposed"] += 1
            logger.info("[propose_norm] 技能 %s → %s（proposal）", name, decision.target_standard)
        else:
            summary["blocked"] += 1
            logger.info("[propose_norm] 技能 %s 拦截: %s", name, gate_reason)
    return summary


def propose(limit: int = DEFAULT_LIMIT, domain: str = "all") -> dict:
    """执行一轮名称归一提议；返回摘要（like shadow worker 语义）。"""
    from app.core.database import neo4j_driver
    from app.services.extraction.llm_provider import LLMConfigurationError, LLMProviderChain

    try:
        llm = LLMProviderChain()
    except LLMConfigurationError:
        return {"status": "skipped", "reason": "LLM 未配置"}

    provider, model = _provider_of(llm)
    run_date = datetime.now(_CST).strftime("%Y-%m-%d")
    max_candidates = limit
    position_budget = max(5, max_candidates // 2)
    skill_budget = max(5, max_candidates - position_budget)

    position: dict = {"candidates": 0, "proposed": 0, "blocked": 0, "llm_failed": 0, "recall_mode": ""}
    skill: dict = {"candidates": 0, "proposed": 0, "blocked": 0, "llm_failed": 0}

    if domain in ("all", "position"):
        pool = _existing_positions(neo4j_driver)
        from app.services.llm_decision.position_name import PositionCandidateRecaller

        recaller = PositionCandidateRecaller(pool, max_candidates)
        position = propose_position(llm, recaller, provider, model, run_date, position_budget)

    if domain in ("all", "skill"):
        skill = propose_skill(llm, provider, model, run_date, skill_budget)

    summary = {
        "status": "ok", "run_date": run_date, "provider": provider, "model": model,
        "position": position, "skill": skill,
    }
    logger.info(
        "[propose_norm] 岗位 候选%d 提议%d 拦截%d / 技能 候选%d 提议%d 拦截%d",
        position["candidates"], position["proposed"], position["blocked"],
        skill["candidates"], skill["proposed"], skill["blocked"],
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="名称归一 LLM 提议（proposal 仅落决策记录）")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--domain", choices=["all", "position", "skill"], default="all")
    args = parser.parse_args()
    summary = propose(limit=args.limit, domain=args.domain)
    print(summary)


if __name__ == "__main__":
    main()
