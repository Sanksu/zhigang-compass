"""差距分析（AL-M4-03，设计文档 §9.5 三态）。

missing：候选人不包含该技能；weak：包含但熟练度不达标；matched：匹配成功。
排序口径：gap_type（missing > weak > matched）内按 skill_weight DESC。
熟练度判定复用匹配引擎 `_skill_similarity` 的同义匹配口径，保证匹配与差距分析一致。
"""

from app.services.learning_path.schemas import GapSkill, GapType
from app.services.matching.engine import _canonical_name
from app.services.matching.weights import load_sim_threshold

# 岗位期望熟练度 → 候选人熟练度下限（候选人熟练度：1 了解 / 2 熟悉 / 3 精通）
_LEVEL_MIN_PROFICIENCY = {"初级": 1, "中级": 2, "高级": 3, "专家": 3}
_PROFICIENCY_NAMES = {1: "了解", 2: "熟悉", 3: "精通"}


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
        差距列表，按 (missing > weak > matched, weight DESC) 排序。
    """
    gaps: list[GapSkill] = []
    for req in [*position.must_skills, *position.nice_skills]:
        sim, matched_skill = _best_matching_skill(
            req, candidate.skills, semantic, sim_threshold
        )
        if sim == 0:
            gap_type = GapType.MISSING
            current = None
        else:
            required_min = _LEVEL_MIN_PROFICIENCY.get(req.proficiency or "")
            if (
                required_min is not None
                and matched_skill is not None
                and matched_skill.proficiency < required_min
            ):
                gap_type = GapType.WEAK
            else:
                gap_type = GapType.MATCHED
            current = _PROFICIENCY_NAMES.get(matched_skill.proficiency) if matched_skill else None

        gaps.append(
            GapSkill(
                skill=req.skill_name,
                skill_id=req.skill_id,
                necessity=req.necessity.value,
                gap_type=gap_type,
                weight=req.weight,
                priority=_priority(gap_type, req.necessity.value),
                current_proficiency=current,
                required_proficiency=req.proficiency,
            )
        )

    order = {GapType.MISSING: 0, GapType.WEAK: 1, GapType.MATCHED: 2}
    gaps.sort(key=lambda g: (order[g.gap_type], -g.weight))
    return gaps
