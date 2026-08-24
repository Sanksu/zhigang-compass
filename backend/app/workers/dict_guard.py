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
from app.services.extraction import llm_invocation
from app.services.extraction.dict_guard import (
    DictGuardDecision,
    build_decision_prompt,
    hard_gate,
    select_dirty_course_edges,
    select_dirty_positions,
    select_isolated_courses,
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
            with llm_invocation.invocation_scope("dict_guard"):
                return await asyncio.to_thread(
                    self._llm.call_with_fallback, prompt, DictGuardDecision,
                )
        except (LLMExtractionError, LLMTimeoutError, self._config_error):
            return None


async def dict_guard_daily(ctx: dict) -> dict:
    """每日字典守卫评估（ETL 阶段任务，ARQ 注册名 dict_guard_daily）。"""
    if not runtime_config.get("dict_guard_enabled", True):
        return {"status": "skipped", "reason": "dict_guard_enabled=false"}

    from sqlalchemy import select

    from app.core.database import async_session_factory, neo4j_driver
    from app.models.business import DictChangeLog, DictProposal
    from app.services.extraction.dictionary import (
        _ALIAS_STANDARDS,
        SKILL_STOPWORDS,
        SKILL_WHITELIST,
    )

    run_date = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    max_candidates = runtime_config.get("dict_guard_max_candidates", 20)

    # ---- 1. 候选生成（规则侧，零或低 LLM 成本）----
    # 技能候选：图谱长尾可疑技能 + 停用词误杀
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

    # 岗位候选：零 REQUIRES 引用的脏岗位（产品名/泛词/业务碎片）
    pos_rows = await asyncio.to_thread(
        _fetch_suspect_positions, neo4j_driver, max_candidates
    )
    dirty_positions = select_dirty_positions(pos_rows)[:max_candidates]

    # 课程候选：完全孤立课程节点 + 同语言脏边（脏边初筛需 SBERT，失败降级跳过）
    course_rows = await asyncio.to_thread(
        _fetch_isolated_courses, neo4j_driver, max_candidates
    )
    isolated_courses = select_isolated_courses(course_rows)[:max_candidates]

    candidates = suspects + misuses + dirty_positions + isolated_courses
    semantic = await asyncio.to_thread(_load_semantic)  # SBERT，可用于课程脏边初筛
    if semantic is not None:
        edge_rows = await asyncio.to_thread(
            _fetch_dirty_edges, neo4j_driver, max_candidates
        )
        candidates += select_dirty_course_edges(edge_rows, semantic)[:max_candidates]

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
        # 候选给出的实体类型为准（position/course 不为空时覆盖 LLM 默认 skill）
        cand_entity = cand.get("entity_type", "skill")
        if cand_entity != "skill":
            decision.entity_type = cand_entity
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
            if dec.action in ("add_stopword", "remove_stopword", "protect_whitelist"):
                dec.entity_type = "skill"  # 技能字典动作强制落到 skill，防不一致
            gate_ok, gate_reason = hard_gate(dec.action, dec.term, dec.entity_type)
            impact = await _estimate_impact(dec.term, dec.entity_type, dec.action)
            impact_nodes = impact.get("graph_nodes", 1)
            tier = tier_for(dec.action, gate_ok, impact_nodes, dec.confidence)

            if tier == "skip":
                skipped.append({"term": dec.term, "action": dec.action,
                                "entity_type": dec.entity_type, "reason": gate_reason})
                continue

            if tier == "auto":
                removed = await asyncio.to_thread(
                    _apply_cleanup, dec.entity_type, dec.action, dec.term,
                    reason=dec.reason,
                )
                impact["removed_units"] = removed
                session.add(DictChangeLog(
                    term=dec.term, action=dec.action, source="auto",
                    kind=_kind_for(dec.action), entity_type=dec.entity_type,
                    reason=dec.reason, impact_stats=impact,
                    detail={"confidence": dec.confidence},
                ))
                auto_applied.append({"term": dec.term, "entity_type": dec.entity_type,
                                     "action": dec.action, "reason": dec.reason, **impact})
                continue

            # proposal 档去重：同 term+action+entity_type 的最近提案——
            # pending 不再重复提议；rejected 进入驳回冷却期（默认 7 天）也不重提
            # （08-24 实证：数据策略/BPEL 驳回次日被每日 ETL 重复提议刷池）
            prior = await session.scalar(
                select(DictProposal).where(
                    DictProposal.term == dec.term,
                    DictProposal.action == dec.action,
                    DictProposal.entity_type == dec.entity_type,
                ).order_by(DictProposal.created_at.desc()).limit(1)
            )
            if prior is not None and _reproposal_blocked(prior, datetime.now(timezone.utc)):
                skipped.append({"term": dec.term, "action": dec.action,
                                "entity_type": dec.entity_type,
                                "reason": _reproposal_skip_reason(prior)})
                continue
            session.add(DictProposal(
                term=dec.term, action=dec.action, status="pending",
                entity_type=dec.entity_type,
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

    # 传 datetime 对象（created_at 为 timestamptz）——字符串字面量会被
    # asyncpg 严格类型拒绝（operator does not exist: timestamptz >= varchar）
    since = datetime.now(timezone.utc) - timedelta(days=_MISUSE_WINDOW_DAYS)
    async with async_session_factory() as session:
        rows = await session.scalars(
            select(JDRaw.raw_text)
            .where(JDRaw.created_at >= since, JDRaw.raw_text.isnot(None), JDRaw.raw_text != "")
            .limit(_MISUSE_SCAN_CAP)
        )
        return "\n".join(rows)


async def _estimate_impact(term: str, entity_type: str = "skill", action: str = "") -> dict:
    """影响面模拟：对应实体图谱节点/边数 + 原文含该词的 JD 数。"""
    from sqlalchemy import func, select

    from app.core.database import async_session_factory, neo4j_driver
    from app.models.raw import JDRaw

    def _count(label: str, name: str) -> int:
        with neo4j_driver.session() as session:
            record = session.run(
                f"MATCH (x:{label}) WHERE x.name = $name RETURN count(x) AS n", name=name
            ).single()
            return record["n"] if record else 0

    async def _jd_hit() -> int:
        async with async_session_factory() as session:
            return await session.scalar(
                select(func.count()).select_from(JDRaw).where(JDRaw.raw_text.contains(term))
            ) or 0

    if entity_type == "course" and action == "remove_edge":
        source, target = term.split("→", 1) if "→" in term else (term, "")
        return {
            "graph_nodes": 1,  # 删边不删节点，单边低影响
            "edge_count": 1, "skill": source, "course": target,
            "jd_snapshots": await _jd_hit(),
        }
    if entity_type == "position":
        graph_nodes = await asyncio.to_thread(_count, "Position", term)
        return {
            "graph_nodes": graph_nodes, "jd_snapshots": await _jd_hit(),
        }
    label = "Skill" if entity_type == "skill" else "Course"
    graph_nodes = await asyncio.to_thread(_count, label, term)
    return {"graph_nodes": graph_nodes, "jd_snapshots": await _jd_hit()}


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


def _load_semantic():
    """SBERT 语义 embedder（课程脏边初筛用）。加载失败/未配置返回 None 降级。"""
    try:
        from app.services.matching.semantic import SkillEmbedder

        return SkillEmbedder.get()
    except Exception:
        logger.warning("SkillEmbedder 加载失败，跳过课程脏边初筛（仅治理孤立课程）")
        return None


def _fetch_suspect_positions(driver, limit: int) -> list[dict]:
    """图谱零引用岗位查询：无 REQUIRES 出边的 Position（长尾碎片候选）。"""
    query = (
        "MATCH (p:Position) "
        "OPTIONAL MATCH (p)-[r:REQUIRES]->() "
        "WITH p, count(r) AS req_count "
        "WHERE req_count = 0 "
        "RETURN p.name AS name, p.first_seen AS first_seen, req_count "
        "ORDER BY p.first_seen DESC LIMIT $limit"
    )
    with driver.session() as session:
        return [dict(record) for record in session.run(query, limit=limit)]


def _fetch_isolated_courses(driver, limit: int) -> list[dict]:
    """完全孤立的课程节点查询（无任何关系，低质主题词课程候选）。"""
    query = (
        "MATCH (c:Course) "
        "WHERE NOT (c)--() "
        "RETURN c.name AS name, c.platform AS platform, c.title AS title, "
        "       0 AS edge_count "
        "ORDER BY c.first_seen DESC LIMIT $limit"
    )
    with driver.session() as session:
        return [dict(record) for record in session.run(query, limit=limit)]


def _fetch_dirty_edges(driver, limit: int) -> list[dict]:
    """查询 LEARNABLE_VIA 边（供 SBERT 初筛后交 LLM 复核是否误配）。"""
    query = (
        "MATCH (s:Skill)-[r:LEARNABLE_VIA]->(c:Course) "
        "RETURN s.name AS skill, c.name AS course, elementId(r) AS rel_id "
        "LIMIT $limit"
    )
    with driver.session() as session:
        return [dict(record) for record in session.run(query, limit=limit)]


def _kind_for(action: str) -> str:
    """变动审计类型：skill 动态条目为 blocked；图谱删除为 node/edge。"""
    if action == "add_stopword":
        return "blocked"
    if action == "remove_edge":
        return "edge"
    if action == "remove_node":
        return "node"
    return "blocked"


def _cleanup_position_node(term: str) -> int:
    """删除脏岗位节点（DETACH 连带 REQUIRES 等边）。"""
    from app.core.database import neo4j_driver

    with neo4j_driver.session() as session:
        record = session.run(
            "MATCH (p:Position {name: $term}) DETACH DELETE p RETURN count(p) AS n",
            term=term,
        ).single()
        return record["n"] if record else 0


def _cleanup_course_node(term: str) -> int:
    """删除孤立脏课程节点（DETACH 连带 LEARNABLE_VIA 等边）。"""
    from app.core.database import neo4j_driver

    with neo4j_driver.session() as session:
        record = session.run(
            "MATCH (c:Course {name: $term}) DETACH DELETE c RETURN count(c) AS n",
            term=term,
        ).single()
        return record["n"] if record else 0


def _cleanup_course_edge(term: str) -> int:
    """删除课程脏边『技能→课程』（LEARNABLE_VIA，不删课程节点）。"""
    from app.core.database import neo4j_driver

    source, target = term.split("→", 1) if "→" in term else (term, "")
    with neo4j_driver.session() as session:
        record = session.run(
            "MATCH (s:Skill {name: $source})-[r:LEARNABLE_VIA]->(c:Course {name: $target}) "
            "DELETE r RETURN count(r) AS n",
            source=source, target=target,
        ).single()
        return record["n"] if record else 0


def _apply_cleanup(entity_type: str, action: str, term: str, *, reason: str) -> int:
    """按 entity_type/action 分派清理动作（auto 档执行）。"""
    if action == "add_stopword":
        from app.services.extraction.dynamic_filters import add_entry as _dyn

        _dyn("blocked", term, reason=reason, source="dict_guard")
        return _cleanup_skill_nodes(term)
    if action == "remove_node":
        return _cleanup_position_node(term) if entity_type == "position" else _cleanup_course_node(term)
    if action == "remove_edge":
        return _cleanup_course_edge(term)
    return 0


def _reproposal_blocked(prior, now: datetime, cooldown_days: int | None = None) -> bool:
    """同 term+action+entity_type 的最近提案是否阻止再次提议。

    - pending：待审中不重复提议（原去重语义）
    - rejected：驳回冷却期内不重提（08-24 修复：驳回次日被 ETL 刷池）；
      冷却期默认取 runtime_config.dict_guard_reproposal_cooldown_days（7）
    - approved / reviewed_at 缺失：允许重新提议（批准后状态变更或历史数据
      不构成阻塞，证据更新可再议）
    """
    if prior is None:
        return False
    if prior.status == "pending":
        return True
    if prior.status != "rejected":
        return False
    if cooldown_days is None:
        from app.core import runtime_config

        cooldown_days = runtime_config.get("dict_guard_reproposal_cooldown_days", 7)
    reviewed_at = prior.reviewed_at
    if reviewed_at is None:
        return False
    cutoff = reviewed_at + timedelta(days=cooldown_days)
    return now <= cutoff


def _reproposal_skip_reason(prior) -> str:
    """跳过理由（留痕到 skipped 报告，供管理面板可见）。"""
    if prior.status == "pending":
        return "已有待审提案"
    return (
        f"驳回冷却期内不重提（驳回于 {prior.reviewed_at:%Y-%m-%d}，"
        f"默认 {runtime_config.get('dict_guard_reproposal_cooldown_days', 7)} 天）"
    )
