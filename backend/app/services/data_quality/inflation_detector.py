"""技能通胀专项检测（设计文档 §4.8）。

四维评分模型：经验 / 技能数量 / 技能深度 / 学历。
每维 0-1，综合 inflation_score 阈值 0.4 / 0.7 分三级：
- <0.4 normal（×1.0）
- 0.4-0.7 mild_inflation（×0.7）
- >0.7 severe_inflation（×0.4）

设计文档未明确四维权重，M2 阶段取均权 0.25 作为框架占位，
M3 用黄金集反向调优后写入 `configs/inflation_weights.json`。
"""

from typing import Literal

from app.services.data_quality.schemas import InflationResult

# ── 设计文档 §4.8 阈值与降权系数 ──
INFLATION_MILD_THRESHOLD = 0.4
INFLATION_SEVERE_THRESHOLD = 0.7
NORMAL_DECAY_WEIGHT = 1.0
MILD_DECAY_WEIGHT = 0.7
SEVERE_DECAY_WEIGHT = 0.4

# ── 岗位级别（与 schemas.py JDExtractionResult.level 对齐）──
JobLevel = Literal["初级", "中级", "高级", "资深", "专家"]

# ── M2 框架占位权重（M3 调优）──
WEIGHTS = {
    "experience": 0.25,
    "skill_count": 0.25,
    "skill_depth": 0.25,
    "education": 0.25,
}

# ── 经验通胀阈值：超出对应年限开始计分（设计文档 §4.8「初级岗要求 10 年大模型经验」是典型样本）──
_EXPERIENCE_CEILINGS: dict[JobLevel, int] = {
    "初级": 3,
    "中级": 5,
    "高级": 8,
    "资深": 10,
    "专家": 12,
}

# ── 技能数量通胀阈值：超出对应数量开始计分 ──
_SKILL_COUNT_CEILINGS: dict[JobLevel, int] = {
    "初级": 5,
    "中级": 8,
    "高级": 12,
    "资深": 15,
    "专家": 20,
}

# ── 学历通胀矩阵：低于岗位级别应有的学历门槛视为通胀 ──
_EDUCATION_RANK = {"不限": 0, "大专": 1, "本科": 2, "硕士": 3, "博士": 4}
_EDUCATION_CEILINGS: dict[JobLevel, int] = {
    "初级": 2,   # 初级岗要求本科合理，要求硕士+即通胀
    "中级": 3,   # 中级岗硕士合理，博士即通胀
    "高级": 3,
    "资深": 4,
    "专家": 4,
}


def compute_experience_score(
    min_years: int, job_level: JobLevel
) -> float:
    """经验维度通胀分。

    低于岗位级别 ceiling 不计通胀；超出部分按线性映射到 [0, 1]，
    每多 3 年增加 0.3 分，>5 年即封顶 1.0（典型场景：初级岗要求 10 年）。
    """
    ceiling = _EXPERIENCE_CEILINGS[job_level]
    if min_years <= ceiling:
        return 0.0
    overflow = min_years - ceiling
    return min(1.0, overflow / 5.0)


def compute_skill_count_score(
    skill_count: int, job_level: JobLevel
) -> float:
    """技能数量维度通胀分。

    超出岗位级别 ceiling 后，每多 3 个技能增加 0.3 分。
    """
    ceiling = _SKILL_COUNT_CEILINGS[job_level]
    if skill_count <= ceiling:
        return 0.0
    overflow = skill_count - ceiling
    return min(1.0, overflow / 6.0)


def compute_skill_depth_score(
    expert_level_count: int, job_level: JobLevel
) -> float:
    """技能深度维度通胀分。

    「精通/专家级」技能数量与岗位级别的错配：
    - 初级岗要求精通 ≥1 项即轻微通胀，≥3 项严重通胀
    - 高级别岗允许更多专家级要求

    参数：
        expert_level_count: JD 中要求「精通/专家」级别的技能数量
    """
    allowed_expert: dict[JobLevel, int] = {
        "初级": 0,
        "中级": 2,
        "高级": 4,
        "资深": 6,
        "专家": 10,
    }
    ceiling = allowed_expert[job_level]
    if expert_level_count <= ceiling:
        return 0.0
    overflow = expert_level_count - ceiling
    return min(1.0, overflow / 3.0)


def compute_education_score(
    education: str, job_level: JobLevel
) -> float:
    """学历维度通胀分。

    低于岗位级别应有的学历门槛视为通胀（如初级岗要求博士）。
    """
    ceiling = _EDUCATION_CEILINGS[job_level]
    edu_rank = _EDUCATION_RANK.get(education, 0)
    if edu_rank <= ceiling:
        return 0.0
    overflow = edu_rank - ceiling
    return min(1.0, overflow / 2.0)


def compute_inflation_score(
    job_level: JobLevel,
    min_years: int,
    skill_count: int,
    expert_level_count: int,
    education: str,
) -> InflationResult:
    """计算四维加权综合通胀指数（设计文档 §4.8）。"""
    exp_score = compute_experience_score(min_years, job_level)
    count_score = compute_skill_count_score(skill_count, job_level)
    depth_score = compute_skill_depth_score(expert_level_count, job_level)
    edu_score = compute_education_score(education, job_level)

    inflation_score = (
        WEIGHTS["experience"] * exp_score
        + WEIGHTS["skill_count"] * count_score
        + WEIGHTS["skill_depth"] * depth_score
        + WEIGHTS["education"] * edu_score
    )

    label = classify_inflation(inflation_score)
    decay = {
        "normal": NORMAL_DECAY_WEIGHT,
        "mild_inflation": MILD_DECAY_WEIGHT,
        "severe_inflation": SEVERE_DECAY_WEIGHT,
    }[label]

    return InflationResult(
        experience_score=exp_score,
        skill_count_score=count_score,
        skill_depth_score=depth_score,
        education_score=edu_score,
        inflation_score=inflation_score,
        label=label,
        decay_weight=decay,
    )


def classify_inflation(score: float) -> Literal[
    "normal", "mild_inflation", "severe_inflation"
]:
    """按阈值划分通胀等级。"""
    if score > INFLATION_SEVERE_THRESHOLD:
        return "severe_inflation"
    if score >= INFLATION_MILD_THRESHOLD:
        return "mild_inflation"
    return "normal"
