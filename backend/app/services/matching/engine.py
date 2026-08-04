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
from app.services.matching.weights import load_sim_threshold, load_weights

# CII 通胀修正阈值与比例（设计文档 9.4 节）
CII_MUST_THRESHOLD = 7          # 必备技能数超过该值才触发降级
CII_DEMOTE_RATIO = 0.2          # 降级最低权重的 20%
CII_PROTECT_PROFICIENCY = "专家"  # 高熟练度核心技能受保护（对齐 schemas 熟练度枚举）
CII_PROTECT_SOURCE_COUNT = 30   # 跨 ≥30 源视为核心技能

# 时效衰减分段（设计文档 9.4 节，基于 LinkedIn IT 技能半衰期估算）
STALENESS_DAYS_MILD = 180
STALENESS_DAYS_STRONG = 365
STALENESS_PENALTY_MILD = 0.95
STALENESS_PENALTY_STRONG = 0.85

# 粗筛保留数（设计文档 9.4 节 Step 1，K=200）
ROUGH_SELECT_K = 200

# LLM 推断软技能命中降权系数（设计文档 9.2 节：low_confidence 匹配时降权 ×0.5）。
# 推断来源（项目角色/经历）置信度低于文本直述，同技能命中计一半分。
SOFT_SKILL_DOWNWEIGHT = 0.5


def _soft_multiplier(cs) -> float:
    """候选技能置信度乘数：LLM 推断软技能 ×0.5，显式技能 ×1.0。"""
    return SOFT_SKILL_DOWNWEIGHT if cs.low_confidence else 1.0


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


def _canonical_name(name: str) -> str:
    """技能规范名：别名归一化 + 中文后缀清洗 + 小写。

    与 JD 入库口径（post_process._clean）一致，保证候选人侧与图谱侧技能名可比。
    别名级同义词（"Golang"→"Go"、"Spring"→"Spring Boot"）由此统一。
    """
    from app.services.extraction.dictionary import normalize_skill
    from app.services.extraction.post_processor import clean_skill_name

    return clean_skill_name(normalize_skill(name)).strip().lower()


def _skill_similarity(
    req,
    candidate_skills,
    semantic=None,
    sim_threshold: float | None = None,
) -> float:
    """技能匹配相似度（设计文档 9.4：别名级 1.0，语义级 0.85-1.0）。

    匹配优先级：skill_id 精确 → 规范名别名同义词（1.0）→ 语义 Embedding 余弦
    （≥ sim_threshold 时计相似度值，否则 0）。semantic 为 None（未注入）或
    模型不可用时退化为纯规则匹配（结果 ∈ {0, 1}），保证现有行为不变。
    """
    req_canon = _canonical_name(req.skill_name)
    for cs in candidate_skills:
        if req.skill_id and cs.skill_id and req.skill_id.lower() == cs.skill_id.lower():
            return _soft_multiplier(cs)
        if req_canon and cs.skill_name and _canonical_name(cs.skill_name) == req_canon:
            return _soft_multiplier(cs)

    if semantic is None or not req.skill_name:
        return 0.0
    threshold = load_sim_threshold() if sim_threshold is None else sim_threshold
    best = 0.0
    best_cs = None
    try:
        for cs in candidate_skills:
            if cs.skill_name:
                sim = semantic.similarity(req.skill_name, cs.skill_name)
                if sim > best:
                    best = sim
                    best_cs = cs
    except Exception:
        # 语义模型不可用（SemanticUnavailableError 等）降级纯规则，不阻断匹配
        return 0.0
    # 阈值基于原始相似度判断（与显式技能口径一致），降权只作用于最终贡献分
    if best_cs is None or best < threshold:
        return 0.0
    return best * _soft_multiplier(best_cs)


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
    semantic=None,
    sim_threshold: float | None = None,
) -> MatchResult:
    """单岗位三维评分（设计文档 9.4 节）。

    - must_score = Σ(sim) / total（别名级 sim=1.0，语义级 sim∈[threshold,1)，
      matched/total 天然惩罚缺失；未注入语义时退化为布尔匹配）
    - nice_score = Σ(sim×weight) / Σ(weight)，岗位无 nice 时取 1.0 不扣分
    - exp_score = min(total_years / required_years, 1.0)，无年限要求时满分
    必备技能全缺失判零（unqualified=True），不纳入推荐排序。

    Args:
        candidate: 候选人画像
        position: 岗位画像（内部先执行 CII 通胀修正）
        weights: (w_must, w_nice, w_exp)，缺省从 configs/match_weights.json 加载
        today: 时效衰减参考日期，仅测试注入，缺省取系统当天
        semantic: Sentence-BERT 相似度器（SkillEmbedder），注入后启用语义同义词匹配
        sim_threshold: 语义命中阈值，None 时从 configs/match_weights.json 读取
    """
    w_must, w_nice, w_exp = weights or load_weights()
    position = apply_cii_correction(position)

    must_total = len(position.must_skills)
    must_sims = [
        _skill_similarity(req, candidate.skills, semantic, sim_threshold)
        for req in position.must_skills
    ]
    matched_must = [
        req.skill_name for req, sim in zip(position.must_skills, must_sims) if sim > 0
    ]
    missing_must = [
        req.skill_name for req, sim in zip(position.must_skills, must_sims) if sim == 0
    ]
    must_score = 1.0 if must_total == 0 else sum(must_sims) / must_total

    nice_total_weight = sum(req.weight for req in position.nice_skills)
    if nice_total_weight == 0:
        nice_score = 1.0
    else:
        nice_score = sum(
            req.weight * _skill_similarity(req, candidate.skills, semantic, sim_threshold)
            for req in position.nice_skills
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

    def __init__(
        self,
        positions: list[PositionProfile] | None = None,
        semantic=None,
        sim_threshold: float | None = None,
    ):
        self._positions = positions or []
        self._semantic = semantic
        self._sim_threshold = sim_threshold

    def match(self, request: MatchRequest) -> list[MatchResult]:
        if request.mode == MatchMode.COMPARE:
            return self._match_compare(request)
        return self._match_auto(request)

    def _match_compare(self, request: MatchRequest) -> list[MatchResult]:
        if not request.target_position_id:
            raise ValueError("COMPARE 模式必须指定 target_position_id")
        for position in self._positions:
            if position.position_id == request.target_position_id:
                return [
                    score_position(
                        request.candidate,
                        position,
                        semantic=self._semantic,
                        sim_threshold=self._sim_threshold,
                    )
                ]
        raise ValueError(f"目标岗位不存在: {request.target_position_id}")

    def _match_auto(self, request: MatchRequest) -> list[MatchResult]:
        scored = [
            score_position(
                request.candidate, p, semantic=self._semantic, sim_threshold=self._sim_threshold
            )
            for p in self._rough_select(request)
        ]
        scored.sort(key=lambda r: r.total_score, reverse=True)
        return scored[: request.top_n]

    def _rough_select(self, request: MatchRequest) -> list[PositionProfile]:
        """Step 1 粗筛：按候选人技能命中数降序取 Top-K。

        候选人 skill_id 为原始名（简历侧未对齐图谱 ID），岗位 skill_id 为图 ID，
        两侧 ID 无交集，故以规范名交集近似（与 _matches 同义词口径一致）。
        """
        candidate_names = {
            _canonical_name(s.skill_name) for s in request.candidate.skills if s.skill_name
        }

        def hit_count(position: PositionProfile) -> int:
            req_names = {_canonical_name(r.skill_name) for r in position.must_skills}
            req_names |= {_canonical_name(r.skill_name) for r in position.nice_skills}
            return len(candidate_names & req_names)

        return sorted(self._positions, key=hit_count, reverse=True)[:ROUGH_SELECT_K]
