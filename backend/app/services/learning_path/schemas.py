"""学习路径数据模型（AL-M4-03，设计文档 §9.5 / §4.6）。"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class GapType(str, Enum):
    """差距类型（设计文档 §9.5 三态）。"""

    MISSING = "missing"
    WEAK = "weak"
    MATCHED = "matched"


class GapSkill(BaseModel):
    """单个技能差距项（设计文档 §9.5）。

    差距优先级按 skill_weight DESC + gap_type（missing > weak）排序，
    matched 仅用于展示（绿色高亮），不进入学习路径。
    """

    skill: str = Field(description="技能名")
    skill_id: Optional[str] = Field(default=None, description="图谱技能 ID（sk_xxxx）")
    necessity: str = Field(description="must / nice")
    gap_type: GapType = Field(description="差距类型：missing / weak / matched")
    weight: float = Field(description="技能权重（REQUIRES.weight，聚合层预计算）")
    priority: str = Field(description="优先级：high / medium / low")
    current_proficiency: Optional[str] = Field(
        default=None, description="候选人当前熟练度：了解/熟悉/精通"
    )
    required_proficiency: Optional[str] = Field(
        default=None, description="岗位期望熟练度：初级/中级/高级/专家"
    )


class CourseRecommendation(BaseModel):
    """学习课程推荐（设计文档 §4.6：学习路径按质量分取 Top-3）。"""

    course_id: str = Field(description="图谱 Course ID（co_xxxx）")
    title: str = Field(description="课程名")
    platform: str = Field(description="课程平台")
    quality_score: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="课程质量分（未评估为 None，排在有分课程之后）"
    )
    recommended: bool = Field(default=False, description="是否进入推荐池（综合分 ≥0.65）")
    source_url: str = Field(default="", description="课程链接")
    hours: Optional[float] = Field(default=None, description="课程时长（小时，无法解析为 None）")


class LearningPathItem(BaseModel):
    """单技能学习路径项（甘特图格式，设计文档 §9.5）。

    输出结构：{skill, prerequisites[], courses[], estimated_hours, priority}。
    """

    skill: str = Field(description="目标技能名")
    skill_id: Optional[str] = Field(default=None, description="图谱技能 ID")
    prerequisites: list[str] = Field(default_factory=list, description="先修技能链（拓扑序，先修在前）")
    courses: list[CourseRecommendation] = Field(default_factory=list, description="推荐课程 Top-3")
    estimated_hours: float = Field(description="预计学习学时（先修链 + 目标技能基础学时之和）")
    priority: str = Field(description="优先级：high / medium / low")


class LearningPathResult(BaseModel):
    """学习路径生成结果。"""

    gaps: list[GapSkill] = Field(default_factory=list, description="差距分析（三态全量，按优先级排序）")
    items: list[LearningPathItem] = Field(default_factory=list, description="学习路径项（仅 missing / weak）")
