"""技能分类审查每日任务：未分类技能 LLM 提议 → 图谱提议字段 + 日报。

链入 run_etl_pipeline（阶段 18，llm_stats 之后），继承主管线当日幂等锁。
只写 `suggested_category*` 提议字段，**不改动权威 category**；LLM 失败
静默跳过不阻塞管线。每条成功提议同步落 llm_decision_records（决策信封，
08-24 统一：domain=skill_classify、status=shadow，供验收统计与抽检）。
报告落 reports/skill_category_review_{date}.json。

红线：prompt/触发门属算法核心（见 services 层 docstring）；晋升通道
（suggested→category）走人工确认，后续 PR 接管理后台。
"""

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core import runtime_config

logger = logging.getLogger(__name__)

_CST = timezone(timedelta(hours=8))
_REPORT_DIR = Path(__file__).resolve().parents[2] / "reports"


async def skill_category_review_daily(ctx: dict) -> dict:
    """技能分类审查（ARQ 注册名 skill_category_review_daily）。"""
    if not runtime_config.get("skill_category_review_enabled", False):
        return {"status": "skipped", "reason": "skill_category_review_enabled=false"}

    from app.services.extraction.skill_category_review import (
        should_classify,
        classify_skill,
    )

    max_candidates = runtime_config.get("skill_category_max_candidates", 20)
    run_date = datetime.now(_CST).strftime("%Y-%m-%d")

    from app.core.database import neo4j_driver

    rows = await asyncio.to_thread(_fetch_unclassified, neo4j_driver)
    candidates = [
        r for r in rows
        if should_classify(
            r.get("name") or "", int(r.get("req_count") or 0),
            has_suggestion=bool(r.get("suggested_category")),
        )
    ][:max_candidates]
    if not candidates:
        summary = {"status": "ok", "run_date": run_date, "candidates": 0,
                   "classified": [], "llm_failed": 0}
        _write_report(summary)
        return summary

    from app.services.extraction.llm_provider import (
        LLMConfigurationError,
        LLMProviderChain,
    )

    try:
        llm = LLMProviderChain()
    except LLMConfigurationError:
        llm = None
    provider, model = _primary_of(llm)

    from app.services.llm_decision import (
        DOMAIN_SKILL_CLASSIFY,
        STATUS_SHADOW,
        build_record,
        persist_record,
    )

    classified: list[dict] = []
    llm_failed = 0
    record_failed = 0
    with neo4j_driver.session() as session:
        for row in candidates:
            name = row["name"]
            result = await asyncio.to_thread(classify_skill, name, llm)
            if result is None:
                llm_failed += 1
                continue
            await asyncio.to_thread(
                _write_suggestion, session, name, result.category,
                result.confidence, result.reason, run_date,
            )
            classified.append({
                "name": name, "category": result.category,
                "confidence": result.confidence, "reason": result.reason,
            })
            # 决策信封（08-24）：每条成功提议同步落 shadow 记录，供验收统计
            record = build_record(
                domain=DOMAIN_SKILL_CLASSIFY,
                entity_type="skill", entity_id=name,
                run_id=f"skill_review:{run_date}",
                input_hash=hashlib.sha256(name.encode("utf-8")).hexdigest(),
                evidence_refs=[{"req_count": int(row.get("req_count") or 0)}],
                provider=provider, model=model,
                structured_output={
                    "category": result.category, "reason": result.reason,
                },
                confidence=result.confidence,
                gate_result="pass",
                risk_tier="R0",  # suggest_category 属 R0 建议类
                status=STATUS_SHADOW,
            )
            try:
                await persist_record(record)
            except Exception as e:
                record_failed += 1
                logger.warning("[skill_category_review] 决策记录落库失败（不影响提议）: %s", e)

    summary = {
        "status": "ok",
        "run_date": run_date,
        "candidates": len(candidates),
        "classified": classified,
        "llm_failed": llm_failed,
        "record_failed": record_failed,
    }
    _write_report(summary)
    logger.info(
        "[skill_category_review] 未分类 %d 个 → 提议 %d 个（失败 %d）",
        len(candidates), len(classified), llm_failed,
    )
    return summary


def _primary_of(llm) -> tuple[str, str]:
    """provider 链主 provider 名称/模型（best-effort，决策信封落档用）。"""
    try:
        primary = (llm._providers or [{}])[0] if llm is not None else {}
        return str(primary.get("name") or ""), str(primary.get("model") or "")
    except Exception:
        return "", ""


def _fetch_unclassified(driver) -> list[dict]:
    """未分类/无 category 技能（引用升序），含既有提议标记供触发门去重。"""
    query = (
        "MATCH (s:Skill) "
        "OPTIONAL MATCH (s)<-[r:REQUIRES]-() "
        "WITH s, count(r) AS req_count "
        "WHERE s.category IS NULL OR s.category = '' OR s.category = '未分类' "
        "RETURN s.name AS name, req_count, s.suggested_category AS suggested_category "
        "ORDER BY req_count ASC LIMIT $pool"
    )
    with driver.session() as session:
        return [dict(record) for record in session.run(query, pool=500)]


def _write_suggestion(session, name: str, category: str, confidence: float,
                      reason: str, run_date: str) -> None:
    """提议写入 suggested_* 字段——权威 category 不动。"""
    session.run(
        "MATCH (s:Skill {name: $name}) "
        "SET s.suggested_category = $category, "
        "    s.suggested_category_confidence = $confidence, "
        "    s.suggested_category_reason = $reason, "
        "    s.suggested_category_at = $at",
        name=name, category=category, confidence=confidence,
        reason=reason, at=run_date,
    )


def _write_report(summary: dict) -> None:
    path = _REPORT_DIR / f"skill_category_review_{summary['run_date']}.json"
    try:
        _REPORT_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except OSError as e:
        logger.warning("skill_category_review 报告写入失败（不影响管线）: %s", e)
