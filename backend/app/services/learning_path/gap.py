"""差距分析（AL-M4-03，设计文档 §9.5 三态）。

missing：候选人不包含该技能；weak：包含但熟练度不达标；matched：匹配成功。
排序口径（设计文档 §9.5 原文「按 skill_weight DESC, gap_type (missing > weak) 排序」）：
skill_weight DESC 优先，权重相同再按 gap_type（missing > weak > matched）。
熟练度判定复用匹配引擎 `_skill_similarity` 的同义匹配口径，保证匹配与差距分析一致。

数据升级（task 2.x，契约 #341）：
- demand：岗位侧 source_count（独立 JD 源数，聚合层预计算）归一化 → 真实需求度；
- trend：技能扩散连续值 = 0.5×min(1,关联岗位数/10) + 0.5×min(1,source_count/20)；
  （替代此前失效的 EVOLVED_FROM 演化信号——技能维度无演化边，改用
  "被更多岗位采用 + 跨更多源"的可解释扩散信号，与 demand 互补）
- roi：(demand × (trend+1)) / cost（cost = base_hours × 熟练度缺口，weak 减半），
  归一化 0-1 输出（08-28 拍板）：原始值域仅 0.0007-0.13，两位小数展示恒为
  0.00 失去信息——以 0.1 为满分基准封顶（≈满需求 20 源 × 满趋势 2.0 ÷ 20 学时
  的速成技能性价比），与 demand/trend 同 0-1 口径，前端 ×100 显示；
- evidence：JD 要求 / 简历现状 溯源（来自岗位要求 + 候选人画像）。
"""

from app.services.learning_path.schemas import GapSkill, GapType, MatchEvidenceItem
from app.services.learning_path.prerequisites import base_hours
from app.services.matching.engine import _canonical_name
from app.services.matching.schemas import SkillRequirement
from app.services.matching.weights import load_sim_threshold
from app.services.proficiency import proficiency_is_weak

_PROFICIENCY_NAMES = {1: "了解", 2: "熟悉", 3: "精通"}

# 数据升级：需求/扩散归一化基准（source_count=20 源或关联岗位=10 视为 1.0）
_DEMAND_NORM = 20.0
_POSITION_DIFFUSION_NORM = 10.0
# ROI 归一化满分基准：原始 roi 达 0.1 即满分封顶（weak 半价学时的热门速成技能可超）
_ROI_FULL_SCORE_REF = 0.1


def _demand_from_source(source_count: int | None) -> float:
    """需求度：独立 JD 源数归一化（min(1, source_count/20)），缺失按 1 源。"""
    return min(1.0, (source_count or 1) / _DEMAND_NORM)


def _position_counts(skill_ids: list[str]) -> dict[str, int]:
    """技能关联岗位数批量查询：图谱 (sk:Skill {id})<-[:REQUIRES]-(p:Position) 计数。

    一次 UNWIND 取全部技能计数（第八轮 P2-13：原先每差距技能一次同步
    Neo4j 往返，compare 详情按技能数线性放大延迟）。无关联岗位的技能
    不出现在返回值中（调用方按 0 取）。图谱不可用不阻断差距分析
    （trend 退化为纯 source_count 项）。
    """
    if not skill_ids:
        return {}
    try:
        from app.core.database import neo4j_driver

        with neo4j_driver.session() as session:
            records = session.run(
                "UNWIND $ids AS sid "
                "MATCH (s:Skill {id: sid})<-[:REQUIRES]-(p:Position) "
                "RETURN sid AS sid, count(p) AS n",
                ids=list(skill_ids),
            ).data()
            return {rec["sid"]: int(rec["n"]) for rec in records}
    except Exception:
        return {}


def _trend_signal(position_count: int, source_count: int | None) -> float:
    """需求趋势连续值（0~1）：技能扩散信号。

    由两项等权合成（全用现有真实数据，可解释）：
    - 岗位扩散：被多少岗位 REQUIRES（≥10 岗位封顶 0.5，计数由
      _position_counts 批量预取）
    - 跨源扩散：被多少独立 JD 源要求（source_count，≥20 源封顶 0.5）
    技能被更多岗位采用且跨更多源 → 需求向上（trend 高）。
    """
    return 0.5 * min(1.0, position_count / _POSITION_DIFFUSION_NORM) + 0.5 * min(
        1.0, (source_count or 1) / _DEMAND_NORM
    )


def _priority(gap_type: GapType, necessity: str) -> str:
    """差距优先级：必备缺失 > 熟练度差距/加分缺失 > 其余。"""
    if gap_type == GapType.MISSING:
        return "high" if necessity == "must" else "medium"
    if gap_type == GapType.WEAK:
        return "medium" if necessity == "must" else "low"
    return "low"


def _best_matching_skill(req, candidate_skills, semantic, sim_threshold):
    """返回 (最高相似度, 对应候选人技能)。

    匹配口径与 matching.engine._skill_similarity 一致：skill_id 精确 → 规范名别名
    同义词（1.0）→ 语义 Embedding 余弦（≥ threshold）。需要返回命中技能以取熟练度，
    故与引擎的纯 sim 计算分离实现（同一优先级顺序）。
    """
    req_canon = _canonical_name(req.skill_name)
    for cs in candidate_skills:
        if req.skill_id and cs.skill_id and req.skill_id.lower() == cs.skill_id.lower():
            return 1.0, cs
        if req_canon and cs.skill_name and _canonical_name(cs.skill_name) == req_canon:
            return 1.0, cs

    if semantic is None or not req.skill_name:
        return 0.0, None
    threshold = load_sim_threshold() if sim_threshold is None else sim_threshold
    best, best_cs = 0.0, None
    try:
        for cs in candidate_skills:
            if cs.skill_name:
                sim = semantic.similarity(req.skill_name, cs.skill_name)
                if sim > best:
                    best, best_cs = sim, cs
    except Exception:
        # 语义模型不可用降级纯规则（与引擎行为一致），不阻断差距分析
        return 0.0, None
    return (best, best_cs) if best >= threshold else (0.0, None)


def analyze_gaps(candidate, position, semantic=None, sim_threshold: float | None = None) -> list[GapSkill]:
    """三态差距分析（设计文档 §9.5）。

    Args:
        candidate: CandidateProfile（候选人画像）
        position: PositionProfile（岗位画像，已含 must/nice 技能要求）
        semantic: Sentence-BERT 相似度器（SkillEmbedder），注入后启用语义同义词匹配
        sim_threshold: 语义命中阈值，None 时从 configs/match_weights.json 读取

    Returns:
        差距列表，按 (weight DESC, missing > weak > matched) 排序。软技能独立
        通道（soft_requirements，不参与评分）同样进入差距列表供展示（is_soft
        打标），但不触发学习路径课程匹配（generator 侧过滤）。

    08-27 fix：入口按 skill_name 去重（must > nice > soft 保留最强优先级），
    避免聚合画像/单 JD 画像三池（must/nice/soft）同名技能重复出现在差距列表。
    """
    # 08-27 fix：must > nice > soft 优先级去重——同名技能保留首个（最强）要求
    seen: dict[str, SkillRequirement] = {}
    for req in [*position.must_skills, *position.nice_skills, *position.soft_requirements]:
        if req.skill_name in seen:
            continue
        seen[req.skill_name] = req

    gaps: list[GapSkill] = []
    # 原始 ROI 留档供 high_roi Top3 排序（roi 字段归一化后封顶会并列，见下）
    roi_raw_by_skill: dict[str, float] = {}
    # 岗位扩散计数一次批量预取（P2-13：避免循环内逐技能同步 Neo4j 往返）
    pos_counts = _position_counts(
        list({req.skill_id for req in seen.values() if req.skill_id})
    )
    for req in seen.values():
        sim, matched_skill = _best_matching_skill(
            req, candidate.skills, semantic, sim_threshold
        )
        if sim == 0:
            gap_type = GapType.MISSING
            current = None
        else:
            if proficiency_is_weak(
                req.proficiency,
                matched_skill.proficiency if matched_skill is not None else None,
            ):
                gap_type = GapType.WEAK
            else:
                gap_type = GapType.MATCHED
            current = _PROFICIENCY_NAMES.get(matched_skill.proficiency) if matched_skill else None

        # ── 数据升级（task 2.x）──
        demand = _demand_from_source(req.source_count)
        trend = _trend_signal(pos_counts.get(req.skill_id, 0), req.source_count)
        # cost：base_hours × 熟练度缺口（missing 全量，weak 减半——与 generator 学时口径一致）
        cost = base_hours(req.skill_name)
        if gap_type == GapType.WEAK:
            cost *= 0.5
        # 归一化输出以 0.1 为满分基准封顶；high_roi Top3 排序仍用原始值，
        # 避免多个超基准缺口被封顶成并列 1.0 后 Top3 选取受列表顺序影响
        roi_raw = (demand * (trend + 1)) / max(cost, 1e-6)
        roi = min(1.0, roi_raw / _ROI_FULL_SCORE_REF)
        roi_raw_by_skill[req.skill_name] = roi_raw
        evidence: list[MatchEvidenceItem] = [
            MatchEvidenceItem(role="jd", text=f"JD 要求：{req.proficiency or '—'}"),
            MatchEvidenceItem(
                role="resume",
                text=(
                    f"简历：已具备（{current}）"
                    if gap_type == GapType.MATCHED and current
                    else "简历：未标注/缺失" if gap_type == GapType.MISSING else f"简历：{current or '—'}"
                ),
            ),
        ]

        gaps.append(
            GapSkill(
                skill=req.skill_name,
                skill_id=req.skill_id,
                necessity=req.necessity.value,
                gap_type=gap_type,
                is_soft=req.is_soft,
                weight=req.weight,
                priority=_priority(gap_type, req.necessity.value),
                current_proficiency=current,
                required_proficiency=req.proficiency,
                demand=demand,
                trend=trend,
                roi=roi,
                evidence=evidence,
            )
        )

    order = {GapType.MISSING: 0, GapType.WEAK: 1, GapType.MATCHED: 2}
    # 设计文档 §9.5：weight DESC 优先，再按 gap_type（missing > weak > matched）
    gaps.sort(key=lambda g: (-g.weight, order[g.gap_type]))
    # 高杠杆缺口打标（task 2.3）：真缺口（missing/weak）按原始 ROI 降序 Top3
    top3 = set(g.skill for g in sorted(
        (g for g in gaps if g.gap_type != GapType.MATCHED),
        key=lambda g: roi_raw_by_skill.get(g.skill, 0.0), reverse=True
    )[:3])
    for g in gaps:
        g.high_roi = g.gap_type != GapType.MATCHED and g.skill in top3
    return gaps
