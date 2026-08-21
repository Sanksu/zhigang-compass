"""dict-guard 每日评估任务：LLM 评估图谱技能数据 → 分级调整字典过滤。

链入 run_etl_pipeline 末段（聚合/快照之后，读到当日最新图谱形态），继承
主管线当日幂等锁；手动补跑走 scripts/cron/dict_guard_daily.py。

流程（技能字典自治守卫方案 §3）：
1. 候选生成（纯规则，零 LLM 成本）：图谱长尾可疑技能 + 停用词误杀检测
2. LLM 单候选评估（Pydantic 强校验；单候选失败跳过并计数，不阻塞管线）
3. 硬门禁（白名单/别名/工具别名互斥一票否决）→ 影响面模拟 → 分级裁决
4. auto：写动态过滤层 + DictChangeLog 审计 + scoped 清理图谱同名节点
   proposal：写 DictProposal(pending) 进审核池（同 term+action 待审去重）
5. 报告落 reports/dict_guard_{date}.json；有自动生效/LLM 全败时 webhook 告警

红线：prompt/门禁阈值/分级规则属算法核心，变更须算法岗张恺天 review。
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core import runtime_config
from app.services.extraction.dict_guard import (
    DictGuardDecision,
    build_decision_prompt,
    hard_gate,
    select_stopword_misuse,
    select_suspect_skills,
    tier_for,
)

logger = logging.getLogger(__name__)

# 停用词语料扫描窗口与行数上限（控制每日 PG 扫描成本）
_MISUSE_WINDOW_DAYS = 7
_MISUSE_SCAN_CAP = 2000


class _DictGuardEvaluator:
    """单候选 LLM 评估器（cluster_llm 同款失败降级：返回 None 表示本轮跳过）。"""

    def __init__(self) -> None:
        from app.services.extraction.llm_provider import (
            LLMConfigurationError,
            LLMProviderChain,
        )

        self._config_error = LLMConfigurationError
        try:
            self._llm = LLMProviderChain()
        except LLMConfigurationError:
            self._llm = None

    async def evaluate(self, candidate: dict) -> DictGuardDecision | None:
        from app.services.extraction.llm_provider import (
            LLMExtractionError,
            LLMTimeoutError,
        )

        if self._llm is None:
            return None
        prompt = build_decision_prompt(candidate)
        try:
            # 异步批量路由契约（§6.5）：30s × provider 优先级链逐个尝试（线程池
            # 执行不阻塞事件循环）。每日批处理非用户实时等待路径，不复用同步
            # 10s 单 provider 契约——主 provider 熔断/边缘延迟会把整轮候选
            # 全部误杀（#306 同款教训：诊断 generator 已因此改走 fallback 链）
            return await asyncio.to_thread(
                self._llm.call_with_fallback, prompt, DictGuardDecision,
            )
        except (LLMExtractionError, LLMTimeoutError, self._config_error):
            return None


async def dict_guard_daily(ctx: dict) -> dict:
    """每日字典守卫评估（ETL 阶段任务，ARQ 注册名 dict_guard_daily）。"""
    if not runtime_config.get("dict_guard_enabled", True):
        return {"status": "skipped", "reason": "dict_guard_enabled=false"}

    from sqlalchemy import func, select

    from app.core.database import async_session_factory, neo4j_driver
    from app.models.business import DictChangeLog, DictProposal
    from app.services.extraction.dictionary import (
        _ALIAS_STANDARDS,
        SKILL_STOPWORDS,
        SKILL_WHITELIST,
    )
    from app.services.extraction.dynamic_filters import add_entry as dyn_add

    run_date = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    max_candidates = runtime_config.get("dict_guard_max_candidates", 20)

    # ---- 1. 候选生成（规则侧，零 LLM 成本）----
    suspect_rows = await asyncio.to_thread(_fetch_suspect_rows, neo4j_driver, max_candidates)
    suspects = select_suspect_skills(suspect_rows)[:max_candidates]

    corpus = await _load_recent_corpus()
    misuses = []
    if corpus:
        # 受保护集合含别名标准名：别名映射的落点（如「大语言模型」）同样是
        # 真实技能，被停用词子串误伤时同样产出 protect/remove 候选（验收建议②）
        misuses = select_stopword_misuse(
            corpus, SKILL_STOPWORDS, SKILL_WHITELIST | _ALIAS_STANDARDS,
        )
        misuses = misuses[:max_candidates]

    candidates = suspects + misuses
    if not candidates:
        summary = {"status": "ok", "run_date": run_date, "candidates": 0,
                   "evaluated": 0, "llm_failed": 0, "auto_applied": [],
                   "proposals": 0, "skipped": []}
        _write_report(summary)
        return summary

    # ---- 2. LLM 逐候选评估 ----
    evaluator = _DictGuardEvaluator()
    decisions: list[DictGuardDecision] = []
    llm_failed = 0
    for cand in candidates:
        decision = await evaluator.evaluate(cand)
        if decision is None:
            llm_failed += 1
            continue
        if not decision.term.strip():
            decision.term = cand["term"]
        decisions.append(decision)

    # ---- 3/4. 门禁 → 影响面 → 分级 → 生效/提案 ----
    # 候选证据随提案持久化（PR-C 审批执行依赖：静态停用词 remove 需从证据
    # 解析「受影响技能」落地为动态 protect）
    cand_evidence = {c["term"]: (c.get("evidence") or {}) for c in candidates}
    auto_applied: list[dict] = []
    proposal_count = 0
    skipped: list[dict] = []
    async with async_session_factory() as session:
        for dec in decisions:
            gate_ok, gate_reason = hard_gate(dec.action, dec.term)
            impact = await _estimate_impact(dec.term)
            tier = tier_for(dec.action, gate_ok, impact["graph_nodes"], dec.confidence)

            if tier == "skip":
                skipped.append({"term": dec.term, "action": dec.action, "reason": gate_reason})
                continue

            if tier == "auto":
                dyn_add("blocked", dec.term, reason=dec.reason, source="dict_guard")
                removed = await asyncio.to_thread(_cleanup_skill_nodes, dec.term)
                impact["removed_nodes"] = removed
                session.add(DictChangeLog(
                    term=dec.term, action=dec.action, source="auto", kind="blocked",
                    reason=dec.reason, impact_stats=impact,
                    detail={"confidence": dec.confidence},
                ))
                auto_applied.append({"term": dec.term, "reason": dec.reason, **impact})
                continue

            # proposal 档：同 term+action 待审去重（多日重复候选不刷屏）
            exists = await session.scalar(
                select(func.count()).select_from(DictProposal).where(
                    DictProposal.term == dec.term,
                    DictProposal.action == dec.action,
                    DictProposal.status == "pending",
                )
            )
            if exists:
                skipped.append({"term": dec.term, "action": dec.action, "reason": "已有待审提案"})
                continue
            session.add(DictProposal(
                term=dec.term, action=dec.action, status="pending",
                reason=dec.reason, llm_confidence=dec.confidence,
                evidence=[
                    {"label": k, "value": v}
                    for k, v in cand_evidence.get(dec.term, {}).items()
                ],
                impact_stats=impact, run_date=run_date,
            ))
            proposal_count += 1
        await session.commit()

    # ---- 5. 报告 + 告警 ----
    summary = {
        "status": "ok",
        "run_date": run_date,
        "candidates": len(candidates),
        "evaluated": len(decisions),
        "llm_failed": llm_failed,
        "auto_applied": auto_applied,
        "proposals": proposal_count,
        "skipped": skipped[:20],
    }
    _write_report(summary)

    if auto_applied or (llm_failed > 0 and not decisions):
        event = "dict_guard_auto_applied" if auto_applied else "dict_guard_llm_down"
        message = (
            f"dict-guard 自动新增停用词 {len(auto_applied)} 条: "
            f"{', '.join(a['term'] for a in auto_applied)}"
            if auto_applied
            else f"dict-guard 本轮 {llm_failed} 个候选全部 LLM 评估失败，未产生任何调整"
        )
        from app.services.alerting import send_alert

        await send_alert(event, f"{message}（详见 reports/dict_guard_{run_date}.json）")
    return summary


def _fetch_suspect_rows(driver, limit: int) -> list[dict]:
    """图谱长尾技能查询：REQUIRES 引用 ≤1 的 Skill（白名单外筛选在服务层做）。"""
    query = (
        "MATCH (s:Skill) "
        "OPTIONAL MATCH (s)<-[r:REQUIRES]-() "
        "WITH s, count(r) AS req_count "
        "WHERE req_count <= 1 "
        "RETURN s.name AS name, s.first_seen AS first_seen, "
        "       s.category AS category, req_count "
        "ORDER BY req_count ASC, s.first_seen DESC LIMIT $limit"
    )
    with driver.session() as session:
        return [dict(record) for record in session.run(query, limit=limit)]


async def _load_recent_corpus() -> str:
    """近 7 天 JD 原文拼接（停用词误杀检测语料，cap 上限控成本）。"""
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.raw import JDRaw

    since = (datetime.now(timezone.utc) - timedelta(days=_MISUSE_WINDOW_DAYS)).strftime("%Y-%m-%d")
    async with async_session_factory() as session:
        rows = await session.scalars(
            select(JDRaw.raw_text)
            .where(JDRaw.created_at >= since, JDRaw.raw_text.isnot(None), JDRaw.raw_text != "")
            .limit(_MISUSE_SCAN_CAP)
        )
        return "\n".join(rows)


async def _estimate_impact(term: str) -> dict:
    """影响面模拟：图谱同名节点数 + 原文含该词的 JD 数。"""
    from sqlalchemy import func, select

    from app.core.database import async_session_factory, neo4j_driver
    from app.models.raw import JDRaw

    def _count_nodes() -> int:
        with neo4j_driver.session() as session:
            record = session.run(
                "MATCH (s:Skill) WHERE s.name = $term RETURN count(s) AS n", term=term
            ).single()
            return record["n"] if record else 0

    graph_nodes = await asyncio.to_thread(_count_nodes)
    async with async_session_factory() as session:
        jd_snapshots = await session.scalar(
            select(func.count()).select_from(JDRaw).where(JDRaw.raw_text.contains(term))
        ) or 0
    return {"graph_nodes": graph_nodes, "jd_snapshots": int(jd_snapshots)}


def _cleanup_skill_nodes(term: str) -> int:
    """scoped 清理：删除与停用词同名的 Skill 节点（DETACH 连带 REQUIRES 边）。"""
    from app.core.database import neo4j_driver

    with neo4j_driver.session() as session:
        record = session.run(
            "MATCH (s:Skill {name: $term}) DETACH DELETE s RETURN count(s) AS n",
            term=term,
        ).single()
        return record["n"] if record else 0


def _write_report(summary: dict) -> None:
    """报告落 backend/reports/dict_guard_{date}.json（幂等覆盖，同 quality 约定）。"""
    report_dir = Path(__file__).resolve().parents[2] / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"dict_guard_{summary['run_date']}.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("[dict_guard_daily] 报告已写入: %s", path)
