"""匹配引擎数据模型（设计文档 9.2 节画像与特征工程）。

岗位画像与候选人画像的统一特征向量设计见设计文档 9.2 节表格。
"""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class Necessity(str, Enum):
    """技能必要性。"""
    MUST = "must"
    NICE = "nice"


class SkillRequirement(BaseModel):
    """岗位侧技能要求。"""
    skill_id: str = Field(description="标准技能 ID（sk_xxxx）")
    skill_name: str = Field(description="标准技能名")
    necessity: Necessity = Field(description="必备 / 加分")
    weight: float = Field(default=1.0, description="技能权重，由聚合层预计算")
    proficiency: Optional[str] = Field(default=None, description="期望熟练度：初级/中级/高级/专家")
    source_count: int = Field(default=1, description="命中该技能的独立 JD 源数")
    is_soft: bool = Field(default=False, description="软技能标记（Position.soft_skills 并入或 Skill.category=软技能；仅展示打标，不影响评分）")


class PositionProfile(BaseModel):
    """岗位画像（设计文档 9.2 节）。

    must_skills / nice_skills 由图谱聚合层预计算，匹配引擎直接消费聚合值。
    soft_requirements 是软技能独立通道（2026-08-22 拍板）：不进 must/nice
    评分池，仅供差距分析展示（is_soft 打标）——匹配评分只算技术栈能力。
    """
    position_id: str
    name: str
    must_skills: list[SkillRequirement] = Field(default_factory=list)
    nice_skills: list[SkillRequirement] = Field(default_factory=list)
    required_years: Optional[float] = Field(default=None, description="经验年限要求")
    required_education: Optional[str] = Field(default=None, description="学历要求")
    required_certs: list[str] = Field(default_factory=list, description="证书要求")
    soft_skills: list[str] = Field(default_factory=list, description="软技能白名单")
    soft_requirements: list[SkillRequirement] = Field(
        default_factory=list,
        description="软技能要求独立通道（不参与评分，仅差距展示；来源=REQUIRES 边软技能类目 + Position.soft_skills）",
    )
    typical_scenarios: list[str] = Field(default_factory=list, description="典型项目场景，用于项目 Embedding 比对")
    industry: Optional[str] = Field(default=None, description="行业（JD 抽取 industry，图谱 Position.industry）")
    last_updated: Optional[str] = Field(default=None, description="岗位聚合最近更新时间 ISO8601，用于时效衰减")


class CandidateSkill(BaseModel):
    """候选人侧技能。"""
    skill_id: str
    skill_name: str
    proficiency: int = Field(ge=1, le=3, description="熟练度：1 了解 / 2 熟悉 / 3 精通")
    low_confidence: bool = Field(default=False, description="LLM 推断的软技能标记，匹配时降权 ×0.5")


class CandidateProject(BaseModel):
    """候选人项目经历。"""
    name: str
    stack: list[str] = Field(default_factory=list)
    description: str = ""


class CandidateProfile(BaseModel):
    """候选人画像（设计文档 9.2 节）。"""
    user_id: str
    skills: list[CandidateSkill] = Field(default_factory=list)
    total_years: float = Field(default=0.0, description="总工作年限")
    education_level: Optional[str] = Field(default=None)
    school_tier: Optional[str] = Field(default=None, description="学校层级：985/211/普通/海外")
    domain_experience: list[str] = Field(default_factory=list, description="领域经验")
    projects: list[CandidateProject] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)


class MatchMode(str, Enum):
    """匹配模式（设计文档 9.1 节）。"""
    AUTO = "auto"      # 自动推荐：遍历全量岗位输出 Top-N
    COMPARE = "compare"  # 人岗比对：单岗位详细比对


class MatchRequest(BaseModel):
    """匹配请求。"""
    candidate: CandidateProfile
    mode: MatchMode = MatchMode.AUTO
    target_position_id: Optional[str] = Field(default=None, description="COMPARE 模式下指定目标岗位")
    top_n: int = Field(default=10, ge=1, le=100, description="AUTO 模式返回数量")
    project_vectors: dict[str, list[float]] = Field(
        default_factory=dict,
        description="项目文本 → 384 维向量（project_embeddings 回填产物），"
        "项目维度评分优先使用，缺失回退 SBERT 文本相似度",
    )


class MatchResult(BaseModel):
    """单岗位匹配结果（设计文档 9.4 节）。"""
    position_id: str
    position_name: str
    total_score: float = Field(ge=0.0, le=1.0)
    must_score: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="必备技能匹配分；岗位无必备技能门槛时为 None（无信息不判分，总分重归一）",
    )
    nice_score: float = Field(ge=0.0, le=1.0)
    exp_score: float = Field(ge=0.0, le=1.0)
    edu_score: Optional[float] = Field(
        default=None, ge=0.0, le=1.0,
        description="学历匹配分（第四维，2026-09-01 BT v4）；任一侧学历层级无法映射时为 None（不参与总分加权）",
    )
    matched_must: list[str] = Field(default_factory=list, description="已匹配的必备技能名")
    missing_must: list[str] = Field(default_factory=list, description="缺失的必备技能名")
    matched_nice: list[str] = Field(
        default_factory=list,
        description="已匹配的加分技能名（JD 证据 hit_count 统一 must+nice 口径）",
    )
    summary: str = Field(default="", description="匹配摘要，供前端展示")
    unqualified: bool = Field(
        default=False,
        description="必备技能全缺失（或无门槛岗位加分技能全未命中）判零时为 True",
    )
    radar: dict = Field(
        default_factory=dict,
        description="人岗比对五维雷达（§9.5）：must/nice/experience/education/projects，"
        "education/projects 无数据时为 None",
    )
