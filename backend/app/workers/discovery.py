"""New-position discovery and technology-watch worker tasks."""

import asyncio
import logging
import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.models.business import DiscoveryCandidate, GraphVersion, TechnologyWatch
from app.models.raw import CommunityRaw, CourseRaw, JDRaw, PaperRaw

logger = logging.getLogger(__name__)

def _candidate_id(skill: str) -> str:
    """候选岗位 id：短名直接截断；超长（>20 字符）技能名加 hash 后缀防截断碰撞。

    存量短名 id 格式不变（cand-xxx）；去重以 position_name 为键，id 变化无兼容问题。
    """
    if len(skill) <= 20:
        return f"cand-{skill}"
    import hashlib

    return f"cand-{skill[:20]}-{hashlib.md5(skill.encode()).hexdigest()[:6]}"


def _position_skill_novelty(
    session, position_names: list[str], reference_days: int | None = None,
) -> dict[str, float | None]:
    """岗位技能新颖度（§7.2.1 skill_novelty < 0.2，08-15 需求调整 0.3→0.2）。

    数据源：Neo4j Skill.first_seen（实测 100% 覆盖）——岗位 REQUIRES 技能
    平均图谱年龄归一化：
        novelty = 1 - min(avg_age_days / reference_days, 1)
    语义：岗位技能平均出现 ≥ reference_days×0.8 天（novelty < 0.2）视为
    技能成熟，才允许 stable（新技能驱动的岗位仍处演化期）。

    reference_days 默认自适应图谱生命周期（today - 图谱最早技能首见时间）：
    固定 365 天在冷启动阶段不适配——图谱仅运行 33 天时全部技能 novelty≈0.99
    （实测），任何岗位都无法 stable；相对口径下图谱首日即有的存量技能
    novelty=0（成熟），近期新增技能 novelty 高（演化期）。

    岗位无技能 / first_seen 全缺失 / 图谱不可达 → None（判定层不拦截，
    保持"novelty 数据不可得时不阻塞"的既有行为）。

    Args:
        session: Neo4j 会话（同步）
        position_names: 岗位名列表
        reference_days: 归一化参考周期（默认 None = 图谱生命周期，可配置）

    Returns:
        {岗位名: novelty | None}
    """
    import logging

    logger = logging.getLogger(__name__)
    try:
        rows = session.run(
            "MATCH (p:Position)-[r:REQUIRES]->(s:Skill) "
            "WHERE p.name IN $names "
            "RETURN p.name AS pname, collect(DISTINCT s.name) AS skills",
            names=list(position_names),
        ).data()
    except Exception as exc:
        logger.warning("_position_skill_novelty: 图谱查询失败: %s", exc)
        return {}

    all_skills = {s for r in rows for s in (r.get("skills") or [])}
    first_seen: dict[str, date] = {}
    if all_skills:
        try:
            recs = session.run(
                "MATCH (s:Skill) WHERE s.name IN $names "
                "RETURN s.name AS name, s.first_seen AS first_seen",
                names=list(all_skills),
            ).data()
            for rec in recs:
                fs = rec.get("first_seen")
                if not fs:
                    continue
                try:
                    first_seen[rec["name"]] = date.fromisoformat(str(fs)[:10])
                except ValueError:
                    continue
        except Exception as exc:
            logger.warning("_position_skill_novelty: first_seen 查询失败: %s", exc)

    today = date.today()
    if reference_days is None:
        # 自适应参考周期 = 图谱生命周期（最早技能首见至今）；首日技能 novelty=0
        earliest = min(first_seen.values(), default=None)
        reference_days = max((today - earliest).days, 1) if earliest else 1
    out: dict[str, float | None] = {}
    for r in rows:
        ages = [
            (today - first_seen[s]).days
            for s in (r.get("skills") or []) if s in first_seen
        ]
        if not ages:
            out[r["pname"]] = None
            continue
        avg_age = sum(ages) / len(ages)
        out[r["pname"]] = 1.0 - min(avg_age / reference_days, 1.0)
    return out


# 项目统一时区 UTC+8（与 services 层常量一致，first_seen/观测起点均按东八区取日期）
_TZ_CN = timezone(timedelta(hours=8))


def _first_seen_date_of(row) -> str:
    """岗位单条 JD 的观测日期（ISO）：post_date 解析日优先，入库日兜底。

    回爬 90 天历史后，存量老岗位的入库日（created_at）是回爬当天，会掩盖其
    真实出现时间，靠发布日（post_date）才能识别为存量；缺失时回退入库日。
    清洗层已把 post_date 归一化（相对时间转绝对 ISO），此处仅截取日期前缀。
    """
    raw = str((row.snapshot or {}).get("post_date") or "").strip()
    m = re.match(r"(\d{4}-\d{2}-\d{2})", raw)
    if m:
        return m.group(1)
    return row.created_at.astimezone(_TZ_CN).date().isoformat()


async def discovery_daily(ctx: dict) -> dict:
    """每日新岗位发现（AL-M4-01，设计文档 7.2.3 节）。

    流程：聚合 jd_raw 已抽取记录 → 计算候选特征（freq/源多样性/Z-score）
    → 阶段一门控（detect_candidates）→ 阶段二 RAG 接地（权威库 + 种子）
    → 幂等 upsert discovery_candidates 候选池 → 自动状态流转持久化。

    幂等设计：按 position_name upsert，重复执行覆盖更新（同岗位不重复入池）。
    """

    from app.core.database import async_session_factory
    from app.services.discovery.detector import DiscoveryDetector, DiscoveryInput
    from app.services.discovery.confidence import compute_confidence
    from app.services.extraction.position_normalization import normalized_position_from_snapshot
    from app.services.discovery.schemas import DiscoveryFeatures

    # ── 1. 聚合 jd_raw 已抽取记录 → 岗位频次/源多样性/首次观测日 ──
    # keyset 游标分批加载，避免全表拉取（与 dedup_simhash 修复一致）
    position_stats: dict[str, dict] = {}
    position_skills: dict[str, set[str]] = {}
    # 系统采集首日（jd_raw 最早入库日，东八区）：post_date 缺失兜底用——
    # 入库日 == 采集首日的岗位视为起步期存量（首日即被采到）
    collection_start = None
    _PAGE = 2000
    last_id = 0
    async with async_session_factory() as session:
        while True:
            batch = (await session.scalars(
                select(JDRaw)
                .where(
                    JDRaw.snapshot["extraction"].astext.isnot(None),
                    JDRaw.id > last_id,
                )
                .order_by(JDRaw.id.asc())
                .limit(_PAGE)
            )).all()
            if not batch:
                break
            for row in batch:
                ext = (row.snapshot or {}).get("extraction") or {}
                name = normalized_position_from_snapshot(row.snapshot)
                if not name:
                    continue
                stat = position_stats.setdefault(
                    name, {"count": 0, "sources": set(), "has_post_date": False}
                )
                stat["count"] += 1
                stat["sources"].add(row.source)
                # post_date 缺失标记：任一记录有真实 post_date 即不算缺失
                # （存量排除兜底见 _is_mature_position）
                if str((row.snapshot or {}).get("post_date") or "").strip():
                    stat["has_post_date"] = True
                # 首次观测日：post_date 解析日优先（回爬老岗位靠发布日识别存量，
                # 避免入库日被回爬当天掩盖），入库日兜底
                fd = _first_seen_date_of(row)
                if stat.get("first_seen") is None or fd < stat["first_seen"]:
                    stat["first_seen"] = fd
                # 采集首日：jd_raw 最早入库日（东八区日期）
                cd = row.created_at.astimezone(_TZ_CN).date().isoformat()
                if collection_start is None or cd < collection_start:
                    collection_start = cd
                # 收集岗位关联技能（供 §7.2.2 辅助加分特征关联 arxiv/github 信号）
                skills = position_skills.setdefault(name, set())
                for s in ext.get("skills") or []:
                    if isinstance(s, dict) and s.get("name"):
                        skills.add(s["name"])
            last_id = batch[-1].id

    if not position_stats:
        return {"candidates": 0, "detail": "无已抽取岗位记录"}

    # ── 2. 组装 DiscoveryInput（Z-score 门控 + 冷启动 Wilson 兜底）──
    # 从 graph_versions 快照序列重建岗位频次窗口，计算真实 Z-score /
    # 3 月移动平均 / 环比增长率，替代此前 history_days=1/z_score=None 硬编码
    # （否则正常 Z-score 门控永不触发，只能走冷启动）
    from app.services.discovery.state_machine import freq_z_scores, position_freq_windows

    async with async_session_factory() as session:
        snap_rows = (await session.scalars(
            select(GraphVersion).order_by(GraphVersion.created_at.asc())
        )).all()
    snapshots = [s.snapshot_json or {} for s in snap_rows]
    freq_windows = position_freq_windows(snapshots, set(position_stats))
    window_days = 0
    observation_start = None
    if snap_rows:
        # 观测窗口起点：首个快照日期（东八区）。成熟岗位排除以此为准——
        # 早于此日期的岗位是系统开始观测前就存在的市场存量
        observation_start = snap_rows[0].created_at.astimezone(_TZ_CN).date().isoformat()
    if len(snap_rows) >= 2:
        span = (snap_rows[-1].created_at - snap_rows[0].created_at)
        window_days = max(span.days, 0) if span else 0

    inputs = []
    # 岗位 → 快照环比增长率（置信度三维加权 §7.2.4 用，见下方 compute_confidence）
    growth_by_position: dict[str, float] = {}
    for name, stat in position_stats.items():
        freq = float(stat["count"])
        freqs = freq_windows.get(name, [])
        # 快照窗口 ≥ 2 期时用真实 Z-score/MA3/环比；否则保持保守冷启动信号
        z_score = None
        growth_rate = 0.0
        jd_freq_ma3 = freq
        # 冷启动二项样本：岗位在快照窗口中的出现密度（默认 0/0 = 快照未出现，
        # 无法冷启动）。口径为"首现后窗口出现率"（successes=出现窗口数，
        # total=首现之后窗口数）而非全量 JD 占比——后者在 JD 占比下 Wilson
        # 下界极低（实测 0.005-0.185），任何阈值都无法通过
        cold_successes, cold_total = 0, 0
        if len(freqs) >= 2:
            zs = freq_z_scores(freqs)
            z_score = float(zs[-1])
            recent3 = freqs[-3:]
            jd_freq_ma3 = sum(recent3) / len(recent3)
            if freqs[-2] > 0:
                growth_rate = (freqs[-1] - freqs[-2]) / freqs[-2]
        if freqs:
            active = sum(1 for f in freqs if f > 0)
            first_active = next((i for i, f in enumerate(freqs) if f > 0), None)
            if first_active is not None:
                cold_successes, cold_total = active, len(freqs) - first_active
        growth_by_position[name] = growth_rate
        inputs.append(
            DiscoveryInput(
                position_name=name,
                features=DiscoveryFeatures(
                    jd_freq_ma3=jd_freq_ma3,
                    z_score=z_score,
                    source_diversity=len(stat["sources"]),
                    first_seen_date=stat.get("first_seen"),
                ),
                history_days=window_days or 1,
                cold_successes=cold_successes,
                cold_total=cold_total,
                first_seen_date=stat.get("first_seen"),
                observation_start=observation_start,
                collection_start=collection_start,
                post_date_missing=not stat.get("has_post_date"),
            )
        )

    # ── 3. 阶段一门控 + 阶段二 RAG 接地 ──
    detector = DiscoveryDetector()
    candidates = detector.detect_candidates(_Provider(inputs))

    # ── 3.1 学术/社区异常信号（设计 §7.2.2 辅助加分特征，M4 接通观察池）──
    # paper_raw(arxiv) / community_raw(github) 过去 12 周聚合 → (技能,源) 周频次，
    # 候选岗位关联技能任一命中 2σ 即标记 arxiv/github_anomaly（仅置信度加分，
    # 不参与 candidate 触发门控，对齐"学术/社区源不独立触发 candidate"）。
    from datetime import date, timedelta

    from app.services.discovery.watch_pool import aggregate_weekly_freqs, anomaly_flags

    since = (date.today() - timedelta(weeks=12)).isoformat()
    async with async_session_factory() as session:
        paper_rows = (await session.scalars(
            select(PaperRaw).where(PaperRaw.crawled_at >= since)
        )).all()
        community_rows = (await session.scalars(
            select(CommunityRaw).where(CommunityRaw.crawled_at >= since)
        )).all()
    academic_freqs = aggregate_weekly_freqs([*paper_rows, *community_rows])

    grounded = []
    # LLM 实例（定义草案中文生成）：未配置 api_key 时 LLMProviderChain 构造
    # 即抛 LLMConfigurationError，fallback 到权威库原文，接地不阻塞。
    from app.services.extraction.jd_extractor import JDExtractor

    llm = None
    try:
        llm = JDExtractor().llm
    except Exception:
        llm = None
    async with async_session_factory() as session:
        for cand in candidates:
            c = await detector.ground_with_rag(cand, session, llm=llm)
            # 置信度：jd_count/source_diversity 来自候选特征，
            # growth_rate 用快照窗口重建的环比增长率（§7.2.4 三维加权公式）
            flags = anomaly_flags(academic_freqs, position_skills.get(cand.position_name, set()))
            conf = compute_confidence(
                jd_count=int(cand.features.jd_freq_ma3),
                source_count=cand.features.source_diversity,
                growth_rate=growth_by_position.get(cand.position_name, 0.0),
                # 学术/社区异常信号（§7.2.2 辅助加分特征，M4 接通观察池）：
                # paper_raw/community_raw 周频次 2σ 判定，仅作置信度加分
                arxiv_anomaly=flags["arxiv"],
                github_anomaly=flags["github"],
            )
            c = c.model_copy(update={"confidence": conf})
            grounded.append(c)
            await _upsert_candidate(session, c)
        await session.commit()

    # 注：自动态迁移（emerging→stable / declining 等）由 discovery_auto_transition
    # 任务负责（依赖 graph_versions 快照序列的窗口频次）；本任务只负责
    # candidate 入池与 RAG 接地。candidate→emerging/rejected 由 admin 审核端点
    # 调用状态机评估。

    return {
        "candidates": len(grounded),
        "seed_matched": sum(1 for c in grounded if c.seed_matched),
        "rag_matched": sum(1 for c in grounded if c.rag_matched),
    }


async def discovery_auto_transition(ctx: dict) -> dict:
    """自动状态流转（设计文档 7.2.1 状态机：emerging/stable/declining 自动迁移）。

    从 jd_raw 已抽取记录按 post_date 聚合岗位 30 天窗口 JD 发布频次（declining
    信号源）→ 对 discovery_candidates 中 state ∈ {emerging, stable, declining}
    的岗位调用 evaluate_auto_transition 判定 → 命中则 PositionStateMachine.persist
    （Neo4j Position.status + 候选池状态）。

    信号源说明（2026-08-11）：declining 信号从"图谱快照 REQUIRES 边数"改为真实
    JD 发布数——快照边数随图谱清理/重建/改名剧烈波动（08-11 重建致"算法工程师"
    1348→56 伪降），而发布数语义 = 设计文档"JD 需求下降"。post_date 缺失按入库
    日兜底（_first_seen_date_of）。

    注意：自动流转 operator="system"，不写 AuditLog（audit_logs.user_id 为
    users 外键，system 无对应用户）。人工流转记录见 /evolution/state-machine。

    emerging → stable: confidence ≥ 0.8 AND 连续 2 窗口波动 < 25% AND 源 ≥ 2
    emerging/stable → declining: 连续 3 窗口频次下降 > 40%
    declining → stable: 连续 2 窗口 z_score > 0（回升）

    幂等：persist 按 name MERGE，重复执行结果一致；无命中不产生副作用。

    数据不足（jd_raw 无已抽取记录或岗位窗口序列 < 2）时跳过，不武断判定（冷启动）。
    """

    from app.core.database import async_session_factory, neo4j_driver
    from app.services.discovery.schemas import CandidatePosition, DiscoveryFeatures, PositionState
    from app.services.discovery.state_machine import (
        WindowFreq, decline_rate, evaluate_auto_transition, freq_z_scores,
        PositionStateMachine, jd_publish_windows, window_volatility,
    )
    from app.services.extraction.dictionary import normalize_position_name
    from app.services.extraction.position_normalization import normalized_position_from_snapshot

    import logging
    _logger = logging.getLogger(__name__)

    # ── 1. 聚合 jd_raw 已抽取记录 → 岗位按天 JD 发布数（declining 信号源）──
    # 一次加载已抽取记录（万级），按 _first_seen_date_of（post_date 解析日优先、
    # 入库日兜底）统计每岗位每日发布数，再切 30 天窗口
    async with async_session_factory() as session:
        jd_rows = (await session.scalars(
            select(JDRaw).where(JDRaw.snapshot["extraction"].astext.isnot(None))
        )).all()

    daily_freqs: dict[str, dict[str, int]] = {}
    for row in jd_rows:
        name = normalized_position_from_snapshot(row.snapshot)
        if not name:
            continue
        day = _first_seen_date_of(row)
        day_counts = daily_freqs.setdefault(name, {})
        day_counts[day] = day_counts.get(day, 0) + 1

    freq_windows = jd_publish_windows(daily_freqs)
    if not freq_windows:
        return {"transitions": 0, "detail": "jd_raw 无已抽取记录，无法计算窗口序列（冷启动）"}

    # ── 2. 对候选池中自动可迁移状态的岗位执行判定 ──
    machine = PositionStateMachine()
    transitions: list[dict] = []
    async with async_session_factory() as session:
        rows = (await session.scalars(
            select(DiscoveryCandidate).where(
                DiscoveryCandidate.state.in_(
                    [PositionState.EMERGING.value, PositionState.STABLE.value, PositionState.DECLINING.value]
                )
            )
        )).all()

        # ── 2.5 skill_novelty（§7.2.1，08-15）：批量查询岗位技能新颖度 ──
        # 数据源：Neo4j Skill.first_seen（100% 覆盖）——岗位 REQUIRES 技能
        # 平均图谱年龄归一化；图谱不可达/无技能返回 None（判定层不拦截）
        novelty_map: dict[str, float | None] = {}
        try:
            with neo4j_driver.session() as neo4j_session:
                novelty_map = await asyncio.to_thread(
                    _position_skill_novelty,
                    neo4j_session,
                    [row.position_name for row in rows],
                )
        except Exception as exc:
            _logger.warning("auto_transition: skill_novelty 查询失败，本次不拦截: %s", exc)

        for row in rows:
            name = normalize_position_name(row.position_name)
            if not name:
                continue
            freqs = freq_windows.get(name, [])
            if len(freqs) < 2:
                _logger.info(
                    "auto_transition 跳过: %s 窗口序列 %s（<2 期，冷启动不武断判定）",
                    row.position_name, freqs,
                )
                continue

            features = DiscoveryFeatures(**row.features)
            candidate = CandidatePosition(
                candidate_id=row.id,
                position_name=row.position_name,
                state=PositionState(row.state),
                features=features,
                detected_at=row.detected_at,
                evidence_refs=row.evidence_refs,
                seed_matched=row.seed_matched,
                rag_matched=row.rag_matched,
                definition_draft=row.definition_draft,
            )
            # z_scores 由频次序列自身重建（freq_z_scores）：declining 岗位回升
            # 时最近 2 窗口 z > 0，触发 declining → stable 自动回迁
            windows = WindowFreq(freqs=freqs, z_scores=freq_z_scores(freqs))
            # jd_count = jd_raw 中该岗位真实 JD 数（§7.2.1 stable 门槛，
            # 08-15 对齐文档：不可用 evidence_refs——发现链路存的是 watch
            # 标记非真实证据，全部候选只有 1 条）
            jd_count = sum(daily_freqs.get(name, {}).values())
            target = evaluate_auto_transition(
                candidate, windows, jd_count=jd_count,
                skill_novelty=novelty_map.get(row.position_name),
            )
            _logger.info(
                "auto_transition: %s state=%s 30天窗口序列=%s z_scores=%s "
                "volatility=%.3f decline_rate=%.3f novelty=%s → %s",
                row.position_name, row.state, freqs,
                [round(z, 3) for z in windows.z_scores],
                window_volatility(windows), decline_rate(windows),
                f"{novelty_map.get(row.position_name):.3f}"
                if novelty_map.get(row.position_name) is not None else "N/A",
                target.value if target else "不迁移",
            )
            if target is None:
                continue

            def _persist_transition() -> CandidatePosition:
                # machine.persist 含同步 Neo4j 写（MERGE + SET status），放线程池
                with neo4j_driver.session() as neo4j_session:
                    return machine.persist(
                        neo4j_session, candidate, target, operator="system",
                    )

            updated = await asyncio.to_thread(_persist_transition)
            row.state = updated.state.value
            transitions.append({
                "position_name": row.position_name,
                "from_state": candidate.state.value,
                "to_state": updated.state.value,
            })
        await session.commit()

    return {
        "transitions": len(transitions),
        "detail": transitions,
    }


class _Provider:
    """适配 CandidateProvider Protocol 的内存数据源。"""

    def __init__(self, inputs):
        self._inputs = inputs

    def iter_inputs(self):
        return iter(self._inputs)


async def _upsert_candidate(session, cand) -> None:
    """按 position_name upsert 候选池（幂等：同岗位覆盖更新特征/状态）。"""

    row = await session.scalar(
        select(DiscoveryCandidate).where(DiscoveryCandidate.position_name == cand.position_name)
    )
    payload = {
        "state": cand.state.value,
        "features": cand.features.model_dump() if hasattr(cand.features, "model_dump") else cand.features,
        "confidence": cand.confidence.model_dump() if cand.confidence else {},
        "evidence_refs": cand.evidence_refs,
        "seed_matched": cand.seed_matched,
        "rag_matched": cand.rag_matched,
        "definition_draft": cand.definition_draft,
        "detected_at": cand.detected_at,
    }
    if row is None:
        session.add(DiscoveryCandidate(id=cand.candidate_id, position_name=cand.position_name, **payload))
    else:
        # 已晋升（emerging/stable/declining 等）的岗位不被 discovery_daily
        # 打回 candidate；仅仍为 candidate 的行允许状态覆盖
        if row.state != "candidate":
            payload.pop("state", None)
        for k, v in payload.items():
            setattr(row, k, v)


# ============================================================
# 技术热点观察池（设计文档 7.2.5）
# ============================================================

async def watch_signal_daily(
    ctx: dict, run_date: str | None = None
) -> dict:
    """每日技术热点信号监测（设计文档 7.2.5 观察池 + MLI 拐点）。

    流程：聚合 4 源 raw 表（jd/course/paper/community）周频次 → 判定
    命中阈值（JD 3 月移动平均环比 > 50%；学术/社区/课程 2σ）→ 幂等
    upsert technology_watch → JD 源命中且该技能此前已在观察池的技能提升
    candidate（写入 discovery_candidates，设计 §7.2.5 / 方案 §2）。

    幂等：technology_watch 按 (skill, source, period) 唯一约束 upsert；
    候选池提升仅对已有观察历史且未晋升的技能生效（不重复提升）。

    Args:
        run_date: 统计周期 YYYY-MM-DD（缺省用当天）
    """
    from datetime import date, timedelta

    from app.core.database import async_session_factory
    from app.services.discovery.watch_pool import (
        aggregate_weekly_freqs,
        anomaly_flags,
        build_signals,
        promotion_features,
    )

    period = run_date or date.today().isoformat()
    # 观察窗口：过去 12 周（JD 3 月移动平均需 12 周以上历史）
    since = (date.fromisoformat(period) - timedelta(weeks=12)).isoformat()

    # ── 1. 读取 4 源 raw 行（crawled_at >= since）──
    async with async_session_factory() as session:
        jd_rows = (await session.scalars(
            select(JDRaw).where(JDRaw.crawled_at >= since)
        )).all()
        course_rows = (await session.scalars(
            select(CourseRaw).where(CourseRaw.crawled_at >= since)
        )).all()
        paper_rows = (await session.scalars(
            select(PaperRaw).where(PaperRaw.crawled_at >= since)
        )).all()
        community_rows = (await session.scalars(
            select(CommunityRaw).where(CommunityRaw.crawled_at >= since)
        )).all()

    all_rows = [*jd_rows, *course_rows, *paper_rows, *community_rows]
    if not all_rows:
        return {"signals": 0, "detail": f"{period} 无 raw 数据"}

    freqs = aggregate_weekly_freqs(all_rows)
    signals = build_signals(freqs, period)
    # 学术/社区源周频次（§7.2.2 辅助加分特征，提升候选置信度加分用）
    academic_freqs = aggregate_weekly_freqs([*paper_rows, *community_rows])

    # ── 2. 幂等 upsert technology_watch + 计算 MLI ──
    promoted: list[str] = []
    upserted = 0
    async with async_session_factory() as session:
        for sig in signals:
            row = await session.scalar(
                select(TechnologyWatch).where(
                    TechnologyWatch.skill_name == sig.skill_name,
                    TechnologyWatch.signal_source == sig.signal_source,
                    TechnologyWatch.period == sig.period,
                )
            )
            if row is None:
                session.add(TechnologyWatch(
                    skill_name=sig.skill_name,
                    signal_source=sig.signal_source,
                    signal_value=sig.signal_value,
                    period=sig.period,
                    status="watch",
                ))
            else:
                row.signal_value = sig.signal_value
                row.last_signal_at = datetime.now(timezone.utc)
            upserted += 1
        await session.commit()

        # ── 3. 提升候选：JD 源命中且该技能此前已在观察池（设计 §7.2.5 / 方案 §2）──
        from app.services.discovery.confidence import compute_confidence
        from app.services.discovery.watch_pool import promotable_skills

        prior_rows = (await session.scalars(
            select(TechnologyWatch.skill_name).where(
                TechnologyWatch.period < period,
            )
        )).all()
        previously_watched = {name for name in prior_rows}

        for skill in promotable_skills(signals, previously_watched):
            existing = await session.scalar(
                select(DiscoveryCandidate).where(
                    DiscoveryCandidate.position_name == skill
                )
            )
            if existing is not None:
                continue  # 已在候选池/已晋升，不重复提升
            # 真实特征与置信度（替代硬编码 source_diversity=1/final_confidence=0.0：
            # 否则提升候选永远无法过 emerging 门槛——跨 ≥2 源 + 置信度 ≥ 0.6）
            feat = promotion_features(freqs, skill)
            flags = anomaly_flags(academic_freqs, {skill})
            conf = compute_confidence(
                jd_count=int(feat["jd_freq_ma3"]),
                source_count=feat["source_diversity"],
                growth_rate=feat["growth"],
                arxiv_anomaly=flags["arxiv"],
                github_anomaly=flags["github"],
            )
            session.add(DiscoveryCandidate(
                id=_candidate_id(skill),
                position_name=skill,
                state="candidate",
                features=feat,  # 键与 DiscoveryFeatures schema 兼容
                confidence=conf.model_dump(),
                evidence_refs=[f"watch:{period}:{skill}"],
                seed_matched=False,
                rag_matched=False,
                definition_draft="",
                detected_at=period,
            ))
            promoted.append(skill)
            # 状态流转：该技能本期 watch 行 → candidate_promoted
            watch_rows = (await session.scalars(
                select(TechnologyWatch).where(
                    TechnologyWatch.skill_name == skill,
                    TechnologyWatch.period == period,
                    TechnologyWatch.status == "watch",
                )
            )).all()
            for r in watch_rows:
                r.status = "candidate_promoted"
        await session.commit()

    return {
        "signals": upserted,
        "promoted": len(promoted),
        "detail": promoted,
    }
