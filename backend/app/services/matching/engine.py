"""匹配引擎实现（设计文档 9.4 节）。

四层主权划分：内容加权 → Sentence-BERT 语义扩展 → 规则引擎（经验/学历/证书）→ LLM 软技能推断。
M2 规则基线：内容加权（must/nice 布尔匹配）+ 经验规则引擎；
Sentence-BERT 语义增强与 Optuna 权重搜索为 M3 交付项（设计文档 9.3 节）。
"""

import logging
import math
from datetime import date, datetime

from app.services.matching.schemas import (
    MatchMode,
    MatchRequest,
    MatchResult,
    Necessity,
    PositionProfile,
)
from app.services.matching.weights import load_domain_sem_blocklist, load_sim_threshold, load_weights

logger = logging.getLogger(__name__)

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


# 熟练度满足度矩阵（方案 A，对齐设计文档 9.2"期望熟练度"匹配）：
# 岗位期望熟练度（聚合层 REQUIRES.level：初级/中级/高级/专家）× 候选人熟练度
# （1 了解 / 2 熟悉 / 3 精通）→ 系数。候选人熟练度 ≥ 岗位期望 → 1.0（不惩罚），
# 低一档 ×0.6、低两档 ×0.3；"了解"对"初级"岗容忍基础接触 ×0.85，
# "精通"对"专家"岗留 ×0.85 余量（避免要求堆到极值把分压到 0）。
_PROFICIENCY_FACTORS: dict[str, dict[int, float]] = {
    "初级": {1: 0.85, 2: 1.0, 3: 1.0},
    "中级": {1: 0.60, 2: 1.0, 3: 1.0},
    "高级": {1: 0.30, 2: 0.60, 3: 1.0},
    "专家": {1: 0.30, 2: 0.60, 3: 0.85},
}


def _proficiency_factor(required: str | None, candidate: int | None) -> float:
    """熟练度满足度系数：候选人熟练度 ≥ 岗位期望 → 1.0，低一档 0.6，低两档 0.3。

    岗位期望缺失（聚合层未写 level / 黄金集构造未传）或候选人熟练度缺失 → 1.0，
    不武断惩罚（黄金集评测零回归的关键默认）。
    """
    if not required or candidate is None:
        return 1.0
    return _PROFICIENCY_FACTORS.get(required, {}).get(candidate, 1.0)


def _source_weight(source_count: int | None) -> float:
    """技能重要性权重：log(source_count+1) 平滑（方案 B）。

    跨源数多的核心必备技能权重更高；source_count 缺失/默认 1 时全技能等权
    （log2），退化为优化前的等权平均（黄金集零回归）。
    """
    return math.log((source_count or 1) + 1)


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
    """技能规范名：canonical_skill_name + 小写。

    与 JD 入库口径（post_process._clean）一致，保证候选人侧与图谱侧技能名可比。
    别名级同义词（"Golang"→"Go"、"Spring"→"Spring Boot"）由此统一。
    """
    from app.services.extraction.post_processor import canonical_skill_name

    return canonical_skill_name(name).strip().lower()


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
        factor = _proficiency_factor(req.proficiency, cs.proficiency)
        if req.skill_id and cs.skill_id and req.skill_id.lower() == cs.skill_id.lower():
            return factor * _soft_multiplier(cs)
        if req_canon and cs.skill_name and _canonical_name(cs.skill_name) == req_canon:
            return factor * _soft_multiplier(cs)

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
    return best * _proficiency_factor(req.proficiency, best_cs.proficiency) * _soft_multiplier(best_cs)


def _proficiency_matched(req, candidate_skills) -> float:
    """命中该岗位技能的候选人熟练度满足度（未命中返回 0.0）。

    仅精确/别名命中判定（与 _skill_similarity 前两路一致），供雷达
    skill_level 维度展示用；语义命中不在此重复计算。
    """
    req_canon = _canonical_name(req.skill_name)
    for cs in candidate_skills:
        if req.skill_id and cs.skill_id and req.skill_id.lower() == cs.skill_id.lower():
            return _proficiency_factor(req.proficiency, cs.proficiency)
        if req_canon and cs.skill_name and _canonical_name(cs.skill_name) == req_canon:
            return _proficiency_factor(req.proficiency, cs.proficiency)
    return 0.0


def _skill_level_score(position: PositionProfile, candidate_skills) -> float | None:
    """雷达"熟练度"维度：命中必备技能中、岗位有期望熟练度要求的满足度均值。

    岗位必备技能均无期望熟练度，或无一命中 → None（无信息不展示，避免误导）。
    仅展示维度，不参与总分。
    """
    factors = []
    for req in position.must_skills:
        if not req.proficiency:
            continue
        factor = _proficiency_matched(req, candidate_skills)
        if factor > 0.0:
            factors.append(factor)
    if not factors:
        return None
    return sum(factors) / len(factors)


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


# 学历层级映射（设计文档 §9.2 学历匹配方法：层级映射 + 学校加权）。
# 关键词包含式匹配，兼容"本科及以上"等表述；仅用于雷达维度近似评分。
_EDU_LEVELS = (
    ("博士", 4),
    ("硕士", 3),
    ("研究生", 3),
    ("本科", 2),
    ("学士", 2),
    ("大专", 1),
    ("专科", 1),
    ("高中", 0),
    ("中专", 0),
)


def _edu_level(text: str | None) -> int | None:
    """学历文本 → 层级分（无法识别返回 None）。"""
    if not text:
        return None
    for keyword, level in _EDU_LEVELS:
        if keyword in text:
            return level
    return None


def _education_score(required: str | None, candidate_level: str | None) -> float | None:
    """雷达"学历"维度近似分（0.0-1.0）：候选人层级 ≥ 岗位要求 → 1.0，
    低一级 → 0.5，其余 → 0.0；任一侧无数据返回 None。

    设计文档 §9.2 学历匹配为"层级映射 + 学校加权"，本次仅实现层级映射的
    保守近似（school_tier 加权待 M5 完善）。
    """
    req = _edu_level(required)
    cand = _edu_level(candidate_level)
    if req is None or cand is None:
        return None
    if cand >= req:
        return 1.0
    if cand == req - 1:
        return 0.5
    return 0.0


def _project_score(candidate, position: PositionProfile, semantic, project_vectors=None) -> float | None:
    """雷达"项目"维度近似分（0.0-1.0）：候选人项目（名+描述）与岗位典型场景
    的语义相似度均值；无项目/岗位无典型场景/语义模型不可用 → None。

    设计文档 §9.2 项目匹配为"项目 Embedding 与场景余弦均值"。向量优先取
    project_embeddings 回填产物（pgvector，project_vectors 入参），未回填时
    回退 SkillEmbedder 文本相似度（同一模型，数值口径一致）。
    岗位侧场景取 PositionProfile.typical_scenarios（聚合层已按需填充，
    当前图谱加载未填时该维度自然返回 None，不阻断比对）。
    """
    projects = [p for p in candidate.projects if p.name or p.description]
    scenarios = [s for s in position.typical_scenarios if s]
    if not projects or not scenarios or semantic is None:
        return None
    project_vectors = project_vectors or {}
    try:
        sims = []
        for project in projects:
            text = project.name + (f"：{project.description}" if project.description else "")
            vec = project_vectors.get(text)
            for scenario in scenarios:
                if vec is not None:
                    # pgvector 回填向量 × 场景查询向量（§11.4.3 project_embeddings）
                    sims.append(semantic.similarity_vec(vec, scenario))
                else:
                    sims.append(semantic.similarity(text, scenario))
        return sum(sims) / len(sims) if sims else None
    except Exception:
        # 语义模型不可用降级（与 _skill_similarity 口径一致），不阻断匹配
        return None


# 领域维度语义阈值：行业词粒度粗（JD 抽取自由文本），远低于技能 sim_threshold(0.85)。
# 实测"电商↔互联网=0.52 / 电商↔金融科技=0.56"，取 0.5 保留合理语义关联而隔离弱噪音。
DOMAIN_SEM_THRESHOLD = 0.5

# 行业词归一化词库：图谱 Position.industry 常见斜杠复合词/别名 → 标准领域词集。
# 词面命中含子串，但"SaaS/云技术"这类复合词拆开后才与候选"云计算"子串命中，
# 此表在子串判定前把复合词拆为原子领域词（词库维度归一化，不改图谱数据）。
_DOMAIN_ALIASES = {
    "saas/云技术": ("云计算", "saas"),
    "银行/金融服务": ("银行", "金融服务"),
    "激光雷达/自动驾驶": ("自动驾驶", "激光雷达"),
    "医疗保健/保险": ("医疗保健", "保险"),
    "国防/航空航天": ("国防", "航空航天"),
    "航天/卫星": ("航天", "卫星"),
    "水利/堤坝巡检": ("水利", "堤坝巡检"),
    "ai agent marketplace": ("人工智能", "ai agent"),
    # 实测图谱值为"电子商务"（电子+商务），候选常写"电商"，字面无子串关系需词库桥接
    "电子商务": ("电商", "电子商务"),
}


def _domain_terms(industry: str) -> list[str]:
    """行业词 → 原子领域词集（归一化 + 斜杠拆分）。

    已收录复合词返回"别名 ∪ 原始拆分段"的并集：别名用于桥接无字面关系的
    同义写法（如"电子商务"→"电商"），原始拆分段保留"云技术"这类候选对
    原始词的子串命中能力。未收录词直接按斜杠拆分。
    """
    key = industry.lower()
    segments = [seg.strip().lower() for seg in industry.split("/") if seg.strip()]
    if key in _DOMAIN_ALIASES:
        return list(dict.fromkeys([*_DOMAIN_ALIASES[key], *segments]))
    return segments


def _domain_score(candidate, position: PositionProfile, semantic=None) -> float | None:
    """雷达"领域"维度近似分（0.0-1.0）：岗位行业 × 候选人领域经验。

    三级判定：
    1. 词面命中：候选领域词与岗位行业（归一化原子词）相等/子串 → 1.0；
    2. 语义兜底：语义余弦 ≥ DOMAIN_SEM_THRESHOLD（0.5，独立于技能阈值）→ 计相似度值；
    3. 未命中 → 0.0。岗位无行业或候选人无领域经验 → None（无信息不参与）。

    设计文档 §9.2 六类特征中的领域匹配：行业词粒度粗、来源多样（JD 抽取
    industry 与简历 domain_experience 均为自由文本），词面 + 语义双路近似，
    不做严格层级映射。本函数每个岗位×候选调用一次，日志记录命中路径便于排查。
    """
    industry = (position.industry or "").strip()
    domains = [d.strip() for d in (candidate.domain_experience or []) if d and d.strip()]
    if not industry or not domains:
        logger.debug(
            "领域匹配跳过 pid=%s industry=%r 候选领域=%r（缺数据）",
            position.position_id, industry, domains,
        )
        return None
    ind_terms = _domain_terms(industry)
    dom_lower = [d.lower() for d in domains]
    # 词面：归一化原子词 vs 候选领域词，相等/子串双向
    for term in ind_terms:
        for d in dom_lower:
            if d == term or d in term or term in d:
                logger.info(
                    "领域词面命中 pid=%s industry=%r 候选=%r term=%r → 1.0",
                    position.position_id, industry, domains, term,
                )
                return 1.0
    if semantic is None:
        logger.debug(
            "领域未命中 pid=%s industry=%r 候选=%r（无语义模型）→ 0.0",
            position.position_id, industry, domains,
        )
        return 0.0
    try:
        # 逐 pair 语义判定：命中黑名单的跨簇对跳过，取剩余最高分
        ind_lower = industry.lower()
        blocklist = load_domain_sem_blocklist()
        best, blocked = 0.0, False
        for d in domains:
            dl = d.lower()
            if frozenset({ind_lower, dl}) in blocklist:
                logger.info(
                    "领域语义黑名单拦截 pid=%s industry=%r × 候选=%r（跨簇假阳性）→ 跳过",
                    position.position_id, industry, d,
                )
                blocked = True
                continue
            sim = semantic.similarity(industry, d)
            if sim > best:
                best = sim
    except Exception:
        # 语义模型不可用降级（与 _skill_similarity 口径一致），不阻断匹配
        logger.warning("领域语义计算失败 pid=%s industry=%r → 0.0", position.position_id, industry)
        return 0.0
    if best >= DOMAIN_SEM_THRESHOLD:
        logger.info(
            "领域语义命中 pid=%s industry=%r 候选=%r best=%.3f ≥阈值%.3f → %.3f",
            position.position_id, industry, domains, best, DOMAIN_SEM_THRESHOLD, best,
        )
        return best
    if blocked:
        logger.info(
            "领域语义未命中（黑名单拦截）pid=%s industry=%r 候选=%r → 0.0",
            position.position_id, industry, domains,
        )
        return 0.0
    logger.debug(
        "领域语义未命中 pid=%s industry=%r 候选=%r best=%.3f <阈值%.3f → 0.0",
        position.position_id, industry, domains, best, DOMAIN_SEM_THRESHOLD,
    )
    return 0.0


def build_radar(
    must_score: float,
    nice_score: float,
    exp_score: float,
    candidate,
    position: PositionProfile,
    semantic=None,
    project_vectors=None,
) -> dict:
    """人岗比对雷达图（设计文档 §9.5：五维 + 熟练度满足度）。

    五维构成（§9.2 六类特征中实现可达的五维）：must（必备技能）/ nice（加分技能）/
    experience（经验）/ education（学历）/ projects（项目），另加 skill_level
    （熟练度满足度，仅展示）与 domain（领域匹配，仅展示）。软技能已并入
    must/nice（low_confidence 降权 ×0.5），证书维度当前图谱侧 required_certs
    数据稀疏暂不入雷达。
    education/projects/domain/skill_level 为保守近似：无数据返回 None，不参与总分。
    """
    skill_level = _skill_level_score(position, candidate.skills)
    return {
        "must": round(must_score, 4),
        "nice": round(nice_score, 4),
        "experience": round(exp_score, 4),
        "education": _education_score(position.required_education, candidate.education_level),
        "projects": _project_score(candidate, position, semantic, project_vectors),
        "domain": _domain_score(candidate, position, semantic),
        "skill_level": round(skill_level, 4) if skill_level is not None else None,
    }


def score_position(
    candidate,
    position: PositionProfile,
    weights: tuple[float, float, float] | None = None,
    today: date | None = None,
    semantic=None,
    sim_threshold: float | None = None,
    project_vectors=None,
) -> MatchResult:
    """单岗位三维评分（设计文档 9.4 节）。

    - must_score = Σ(w×sim) / Σ(w)（w = log(source_count+1)，别名级 sim=1.0，
      语义级 sim∈[threshold,1)；Σ(w×sim)/Σ(w) 天然惩罚缺失，未注入语义时退化为
      布尔匹配；source_count 默认 1 时等权退化）
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
    # must 按 source_count 加权（log(source_count+1)）：跨源多的核心技能贡献/缺失
    # 惩罚均更重；source_count 默认 1 时等权，退化为优化前行为（黄金集零回归）
    must_weights = [_source_weight(req.source_count) for req in position.must_skills]
    must_total_weight = sum(must_weights)
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
    must_score = (
        1.0
        if must_total_weight == 0
        else sum(
            w * sim for w, sim in zip(must_weights, must_sims)
        ) / must_total_weight
    )

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
        radar=build_radar(
            must_score, nice_score, exp_score, candidate, position, semantic, project_vectors
        ),
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
                        project_vectors=request.project_vectors,
                    )
                ]
        raise ValueError(f"目标岗位不存在: {request.target_position_id}")

    def _match_auto(self, request: MatchRequest) -> list[MatchResult]:
        scored = [
            score_position(
                request.candidate, p, semantic=self._semantic,
                sim_threshold=self._sim_threshold, project_vectors=request.project_vectors,
            )
            for p in self._rough_select(request)
        ]
        # 必备技能全缺失（unqualified）不纳入推荐排序（设计文档 §9.4）
        scored = [r for r in scored if not r.unqualified]
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
