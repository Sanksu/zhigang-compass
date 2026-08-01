"""匹配引擎实现（设计文档 9.4 节）。

四层主权划分：内容加权 → Sentence-BERT 语义扩展 → 规则引擎（经验/学历/证书）→ LLM 软技能推断。
M2 规则基线：内容加权（must/nice 布尔匹配）+ 经验规则引擎；
Sentence-BERT 语义增强与 Optuna 权重搜索为 M3 交付项（设计文档 9.3 节）。
"""

from datetime import date, datetime

from app.services.matching.schemas import (
    MatchMode,
    MatchRequest,
    MatchResult,
    Necessity,
    PositionProfile,
)
from app.services.matching.weights import load_weights

# CII 通胀修正阈值与比例（设计文档 9.4 节）
CII_MUST_THRESHOLD = 7          # 必备技能数超过该值才触发降级
CII_DEMOTE_RATIO = 0.2          # 降级最低权重的 20%
CII_PROTECT_PROFICIENCY = "精通"  # 高熟练度核心技能受保护
CII_PROTECT_SOURCE_COUNT = 30   # 跨 ≥30 源视为核心技能

# 时效衰减分段（设计文档 9.4 节，基于 LinkedIn IT 技能半衰期估算）
STALENESS_DAYS_MILD = 180
STALENESS_DAYS_STRONG = 365
STALENESS_PENALTY_MILD = 0.95
STALENESS_PENALTY_STRONG = 0.85

# 粗筛保留数（设计文档 9.4 节 Step 1，K=200）
ROUGH_SELECT_K = 200


def apply_cii_correction(position: PositionProfile) -> PositionProfile:
    """CII 通胀修正：必备技能数 > 7 时降级最低权重 20% 为加分技能。

    避免"初级岗要求 10 年大模型经验"式虚高要求导致匹配分虚低。
    精通且跨 ≥30 源的核心技能不降级。无通胀时原样返回。
    """
    if len(position.must_skills) <= CII_MUST_THRESHOLD:
        return position

    demote_count = max(1, int(len(position.must_skills) * CII_DEMOTE_RATIO))
    demote_pool = sorted(position.must_skills, key=lambda s: (s.weight, s.source_count))

    demoted_ids: set[int] = set()
    for skill in demote_pool:
        if len(demoted_ids) >= demote_count:
            break
        if (
            skill.proficiency == CII_PROTECT_PROFICIENCY
            and skill.source_count >= CII_PROTECT_SOURCE_COUNT
        ):
            continue
        demoted_ids.add(id(skill))

    new_must = [s for s in position.must_skills if id(s) not in demoted_ids]
    new_nice = position.nice_skills + [
        s.model_copy(update={"necessity": Necessity.NICE})
        for s in position.must_skills
        if id(s) in demoted_ids
    ]
    return position.model_copy(update={"must_skills": new_must, "nice_skills": new_nice})


def staleness_penalty(last_updated: str | None, today: date | None = None) -> float:
    """时效衰减系数：岗位聚合超过 180/365 天分别降权 0.95/0.85。

    last_updated 缺失或无法解析时视为新鲜（系数 1.0），聚合层未提供更新时间
    属于合法状态，不阻断匹配。
    """
    if not last_updated:
        return 1.0
    try:
        updated = datetime.fromisoformat(last_updated).date()
    except ValueError:
        return 1.0
    days = (today or date.today()) - updated
    if days.days > STALENESS_DAYS_STRONG:
        return STALENESS_PENALTY_STRONG
    if days.days > STALENESS_DAYS_MILD:
        return STALENESS_PENALTY_MILD
    return 1.0


def _matches(req, candidate_skills) -> bool:
    """技能匹配：skill_id 或 skill_name 命中任一候选人技能（M2 布尔匹配）。

    M3 接入 Sentence-BERT 后，此处在 name 相似度 ≥ sim_threshold 时亦可命中。
    """
    for cs in candidate_skills:
        if req.skill_id and cs.skill_id and req.skill_id.lower() == cs.skill_id.lower():
            return True
        if req.skill_name and cs.skill_name and req.skill_name.lower() == cs.skill_name.lower():
            return True
    return False


def _build_summary(
    position_name: str,
    matched_must: list[str],
    missing_must: list[str],
    must_score: float,
    unqualified: bool,
) -> str:
    if unqualified:
        return f"{position_name}：必备技能全缺失，未达门槛"
    parts = [f"{position_name}：必备技能命中 {must_score:.0%}"]
    if matched_must:
        parts.append("已具备 " + "、".join(matched_must[:5]))
    if missing_must:
        parts.append("缺口 " + "、".join(missing_must[:5]))
    return "；".join(parts)


def score_position(
    candidate,
    position: PositionProfile,
    weights: tuple[float, float, float] | None = None,
    today: date | None = None,
) -> MatchResult:
    """单岗位三维评分（设计文档 9.4 节）。

    - must_score = matched / total（matched/total 天然惩罚缺失）
    - nice_score = Σ(sim×weight) / Σ(weight)，M2 sim∈{0,1}；岗位无 nice 时取 1.0 不扣分
    - exp_score = min(total_years / required_years, 1.0)，无年限要求时满分
    必备技能全缺失判零（unqualified=True），不纳入推荐排序。

    Args:
        candidate: 候选人画像
        position: 岗位画像（内部先执行 CII 通胀修正）
        weights: (w_must, w_nice, w_exp)，缺省从 configs/match_weights.json 加载
        today: 时效衰减参考日期，仅测试注入，缺省取系统当天
    """
    w_must, w_nice, w_exp = weights or load_weights()
    position = apply_cii_correction(position)

    must_total = len(position.must_skills)
    matched_must = [
        req.skill_name for req in position.must_skills if _matches(req, candidate.skills)
    ]
    missing_must = [
        req.skill_name for req in position.must_skills if not _matches(req, candidate.skills)
    ]
    must_score = 1.0 if must_total == 0 else len(matched_must) / must_total

    nice_total_weight = sum(req.weight for req in position.nice_skills)
    if nice_total_weight == 0:
        nice_score = 1.0
    else:
        nice_score = sum(
            req.weight for req in position.nice_skills if _matches(req, candidate.skills)
        ) / nice_total_weight

    if position.required_years is None or position.required_years <= 0:
        exp_score = 1.0
    elif candidate.total_years >= position.required_years:
        exp_score = 1.0
    else:
        exp_score = candidate.total_years / position.required_years

    base_total = must_score * w_must + nice_score * w_nice + exp_score * w_exp

    if must_total > 0 and must_score == 0.0:
        total = 0.0
        unqualified = True
    else:
        total = base_total * staleness_penalty(position.last_updated, today)
        unqualified = False

    return MatchResult(
        position_id=position.position_id,
        position_name=position.name,
        total_score=round(total, 4),
        must_score=round(must_score, 4),
        nice_score=round(nice_score, 4),
        exp_score=round(exp_score, 4),
        matched_must=matched_must,
        missing_must=missing_must,
        summary=_build_summary(position.name, matched_must, missing_must, must_score, unqualified),
        unqualified=unqualified,
    )


class MatchEngine:
    """匹配引擎接口（设计文档 9.4 节）。

    四层主权划分：内容加权 → Sentence-BERT 语义扩展 → 规则引擎（经验/学历/证书）→ LLM 软技能推断。
    """

    def match(self, request: MatchRequest) -> list[MatchResult]:
        """执行匹配，返回 Top-N 结果列表。

        AUTO 模式：遍历岗位集返回 Top-N；COMPARE 模式：返回单岗位详细比对。
        """
        raise NotImplementedError


class RuleBasedMatcher(MatchEngine):
    """规则基线匹配器（设计文档 9.3/9.4 节）。

    构造时注入岗位画像列表（聚合层产出）。M3 接入图谱/PostgreSQL 后由聚合层
    查询提供岗位集，匹配逻辑本身保持纯计算。

    Args:
        positions: 全量岗位画像；缺省为空列表（AUTO 模式无岗位可推荐时返回空）。
    """

    def __init__(self, positions: list[PositionProfile] | None = None):
        self._positions = positions or []

    def match(self, request: MatchRequest) -> list[MatchResult]:
        if request.mode == MatchMode.COMPARE:
            return self._match_compare(request)
        return self._match_auto(request)

    def _match_compare(self, request: MatchRequest) -> list[MatchResult]:
        if not request.target_position_id:
            raise ValueError("COMPARE 模式必须指定 target_position_id")
        for position in self._positions:
            if position.position_id == request.target_position_id:
                return [score_position(request.candidate, position)]
        raise ValueError(f"目标岗位不存在: {request.target_position_id}")

    def _match_auto(self, request: MatchRequest) -> list[MatchResult]:
        scored = [score_position(request.candidate, p) for p in self._rough_select(request)]
        scored.sort(key=lambda r: r.total_score, reverse=True)
        return scored[: request.top_n]

    def _rough_select(self, request: MatchRequest) -> list[PositionProfile]:
        """Step 1 粗筛：按候选人技能命中数降序取 Top-K。

        无倒排索引时以"岗位技能 ID ∩ 候选人技能 ID"的交集数近似。
        """
        candidate_ids = {s.skill_id for s in request.candidate.skills}

        def hit_count(position: PositionProfile) -> int:
            skill_ids = {r.skill_id for r in position.must_skills} | {
                r.skill_id for r in position.nice_skills
            }
            return len(candidate_ids & skill_ids)

        return sorted(self._positions, key=hit_count, reverse=True)[:ROUGH_SELECT_K]
