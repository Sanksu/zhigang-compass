"""名称归一 LLM 影子审查每日任务（PR3b：shadow 不产生任何生产写入）。

链入 run_etl_pipeline 阶段 19（skill_category_review 之后），继承主管线
当日幂等锁。默认关闭（runtime_config.name_normalization_shadow_enabled）。
对两类输入运行 LLM 归一决策并落 llm_decision_records（status=shadow：
tier/gate 档位落档但不生效），供验收窗口统计精度与人工抽检。

- 岗位名：最近抽取 JD（title + 抽取技能 + 来源 + 图谱候选岗位名）
- 技能名：图谱未归一化技能（normalized_name 缺失，低引用优先）

红线：prompt/硬门在 services/llm_decision/ 决策器，属算法核心（张恺天 review）。
"""

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core import runtime_config
from app.services.extraction import llm_invocation
from app.services.llm_decision import persist_record, build_record

logger = logging.getLogger(__name__)

_CST = timezone(timedelta(hours=8))
_REPORT_DIR = Path(__file__).resolve().parents[2] / "reports"
# 已抽取 JD 扫描窗口（天）：只审最近窗口内的新语义输入，控制每日 LLM 成本
_JD_RECENT_DAYS = 2


def _input_hash(title: str, skills: list[str]) -> str:
    return hashlib.sha256(
        json.dumps({"title": title or "", "skills": skills or []}, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _primary_of(llm) -> tuple[str, str]:
    """当前 provider 链主 provider 名称/模型（best-effort，供决策信封落档）。"""
    try:
        primary = (llm._providers or [{}])[0]
        return str(primary.get("name") or ""), str(primary.get("model") or "")
    except Exception:
        return "", ""


def _skills_from_extraction(extraction: dict) -> list[str]:
    skills: list[str] = []
    for s in (extraction.get("skills") or []):
        if isinstance(s, dict) and s.get("name"):
            skills.append(str(s["name"]))
    for req in (extraction.get("requirements") or []):
        if isinstance(req, dict) and req.get("skill_name"):
            skills.append(str(req["skill_name"]))
    return skills


async def name_normalization_shadow_daily(ctx: dict) -> dict:
    """名称归一影子审查（ARQ 注册名 name_normalization_shadow_daily）。"""
    if not runtime_config.get("name_normalization_shadow_enabled", False):
        return {"status": "skipped", "reason": "name_normalization_shadow_enabled=false"}

    from app.services.extraction.llm_provider import LLMConfigurationError, LLMProviderChain
    from app.services.llm_decision import (
        DOMAIN_POSITION_NORMALIZE,
        DOMAIN_SKILL_NORMALIZE,
        STATUS_SHADOW,
    )
    from app.services.llm_decision.position_name import (
        decide_position_name,
        position_name_gate,
        tier_for_position_decision,
    )
    from app.services.llm_decision.skill_normalize import (
        decide_skill_normalize,
        skill_normalize_gate,
        tier_for_skill_decision,
    )

    try:
        llm = LLMProviderChain()
    except LLMConfigurationError:
        return {"status": "skipped", "reason": "LLM 未配置"}

    max_candidates = runtime_config.get("name_normalization_max_candidates", 20)
    position_budget = max_candidates // 2
    skill_budget = max_candidates - position_budget
    run_date = datetime.now(_CST).strftime("%Y-%m-%d")
    provider, model = _primary_of(llm)

    from app.core.database import async_session_factory, neo4j_driver
    from app.models.raw import JDRaw
    from sqlalchemy import select

    # ---- 岗位名影子 ----
    position_summary: dict = {"candidates": 0, "recorded": 0, "llm_failed": 0, "blocked": 0}
    async with async_session_factory() as session:
        rows = (await session.scalars(
            select(JDRaw)
            .where(JDRaw.snapshot["extraction"].astext.is_not(None))
            .order_by(JDRaw.updated_at.desc())
            .limit(position_budget)
        )).all()
    candidates = await asyncio.to_thread(_existing_positions, neo4j_driver)
    for row in rows:
        snap = row.snapshot or {}
        extraction = snap.get("extraction") or {}
        title = str(snap.get("title") or "").strip()
        if not title:
            continue
        position_summary["candidates"] += 1
        skills = _skills_from_extraction(extraction)
        with llm_invocation.invocation_scope(
            "position_normalize_shadow", run_id=f"shadow:{run_date}",
        ):
            decision = await asyncio.to_thread(
                decide_position_name, title, skills,
                str(row.source or ""), candidates, llm,
            )
        if decision is None:
            position_summary["llm_failed"] += 1
            continue
        gate_ok, gate_reason = position_name_gate(decision, title, candidates)
        tier, _ = tier_for_position_decision(decision, gate_ok)
        record = build_record(
            domain=DOMAIN_POSITION_NORMALIZE,
            entity_type="jd", entity_id=str(row.id), run_id=f"shadow:{run_date}",
            input_hash=_input_hash(title, skills),
            evidence_refs=[{"source": row.source, "source_url": row.source_url}],
            provider=provider, model=model,
            structured_output=decision.model_dump(),
            confidence=decision.confidence,
            gate_result="blocked" if not gate_ok else "pass",
            risk_tier=tier,
            status=STATUS_SHADOW,
        )
        await persist_record(record)
        position_summary["recorded"] += 1
        if not gate_ok:
            position_summary["blocked"] += 1
            logger.info("[name_shadow] 岗位 %s 硬门拦截: %s", title, gate_reason)

    # ---- 技能名影子 ----
    skill_summary: dict = {"candidates": 0, "recorded": 0, "llm_failed": 0, "blocked": 0}
    skill_rows = await asyncio.to_thread(_fetch_unormalized_skills, neo4j_driver, skill_budget)
    for skill_row in skill_rows:
        name = str(skill_row.get("name") or "").strip()
        if not name:
            continue
        skill_summary["candidates"] += 1
        with llm_invocation.invocation_scope(
            "skill_normalize_shadow", run_id=f"shadow:{run_date}",
            entity_ref=f"skill:{name[:40]}",
        ):
            decision = await asyncio.to_thread(decide_skill_normalize, name, llm)
        if decision is None:
            skill_summary["llm_failed"] += 1
            continue
        gate_ok, gate_reason = skill_normalize_gate(decision, name)
        tier, _ = tier_for_skill_decision(decision, gate_ok)
        record = build_record(
            domain=DOMAIN_SKILL_NORMALIZE,
            entity_type="skill", entity_id=name, run_id=f"shadow:{run_date}",
            input_hash=hashlib.sha256(name.encode("utf-8")).hexdigest(),
            provider=provider, model=model,
            structured_output=decision.model_dump(),
            confidence=decision.confidence,
            gate_result="blocked" if not gate_ok else "pass",
            risk_tier=tier,
            status=STATUS_SHADOW,
        )
        await persist_record(record)
        skill_summary["recorded"] += 1
        if not gate_ok:
            skill_summary["blocked"] += 1
            logger.info("[name_shadow] 技能 %s 硬门拦截: %s", name, gate_reason)

    summary = {
        "status": "ok",
        "run_date": run_date,
        "provider": provider,
        "model": model,
        "position": position_summary,
        "skill": skill_summary,
    }
    _write_report(summary)
    logger.info(
        "[name_normalization_shadow] 岗位 %d 记录 %d / 技能 %d 记录 %d",
        position_summary["candidates"], position_summary["recorded"],
        skill_summary["candidates"], skill_summary["recorded"],
    )
    return summary


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


def _write_report(summary: dict) -> None:
    path = _REPORT_DIR / f"name_normalization_shadow_{summary['run_date']}.json"
    try:
        _REPORT_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except OSError as e:
        logger.warning("name_normalization_shadow 报告写入失败（不影响管线）: %s", e)