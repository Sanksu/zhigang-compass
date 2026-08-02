"""数据质量检测的数据模型。

字段对齐设计文档 §4.7（SAI/僵尸 JD/抄袭时滞）与 §4.8（通胀四维）。
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class JDSkillSet(BaseModel):
    """单条 JD 的技能集合视图。

    用于时滞/通胀检测的输入。技能 age（天）来自图谱层 `Skill.first_seen_at`，
    M2 阶段未接入图谱，调用方需以 mock 数据填充。
    """

    jd_id: str = Field(description="JD 唯一标识（source + source_id）")
    position_name: str = Field(description="岗位名称，用于聚合同岗位历史")
    company: str | None = Field(default=None, description="公司名，僵尸 JD 检测需要")
    publish_date: date = Field(description="发布日期，用于历史窗口与时间间隔判断")
    skills: list[str] = Field(default_factory=list, description="技能名（已归一化）")
    skill_ages_days: list[int] = Field(
        default_factory=list,
        description="每个技能的首见时长（天），与 skills 一一对应",
    )


class SAIResult(BaseModel):
    """技能老化指数（SAI）检测结果。"""

    sai: float = Field(description="技能老化指数：JD 技能年龄中位数 / 同岗位近 90 天中位数")
    label: Literal["fresh", "content_stale", "content_obsolete"] = Field(
        description="时滞等级：SAI≤1.5 fresh / 1.5<SAI≤2.0 stale / >2.0 obsolete"
    )
    decay_weight: float = Field(description="降权系数：fresh=1.0 / stale=0.5 / obsolete=0（不入聚合）")


class ZombieJDResult(BaseModel):
    """僵尸 JD 检测结果。"""

    is_zombie: bool = Field(description="是否为僵尸 JD")
    jaccard: float = Field(description="与历史版本技能集合的 Jaccard 相似度")
    consecutive_periods: int = Field(description="连续相似周期数（≥ N 触发）")
    decay_weight: float = Field(description="降权系数：zombie=0.3，非 zombie=1.0")


class PlagiarismResult(BaseModel):
    """抄袭时滞检测结果。"""

    is_plagiarism: bool = Field(description="是否为抄袭改日期")
    is_subset: bool = Field(description="新 JD 技能是否为旧 JD 子集")
    days_interval: int = Field(description="与源 JD 的发布时间间隔（天）")
    decay_weight: float = Field(description="降权系数：plagiarism=0.4，非=1.0")


class InflationResult(BaseModel):
    """技能通胀检测结果（设计文档 §4.8 四维加权）。"""

    experience_score: float = Field(ge=0.0, le=1.0, description="经验维度通胀分")
    skill_count_score: float = Field(ge=0.0, le=1.0, description="技能数量维度通胀分")
    skill_depth_score: float = Field(default=0.0, ge=0.0, le=1.0, description="技能深度维度通胀分")
    education_score: float = Field(ge=0.0, le=1.0, description="学历维度通胀分")
    inflation_score: float = Field(ge=0.0, le=1.0, description="综合通胀指数（四维加权）")
    label: Literal["normal", "mild_inflation", "severe_inflation"] = Field(
        description="通胀等级：<0.4 normal / 0.4-0.7 mild / >0.7 severe"
    )
    decay_weight: float = Field(description="降权系数：normal=1.0 / mild=0.7 / severe=0.4")


class CourseQualityResult(BaseModel):
    """课程质量评估结果（设计文档 §4.6 六维加权）。

    综合分 = 平台权威性×0.25 + 用户评分×0.20 + 注册量与完成率×0.15
             + 时效性×0.20 + 技能覆盖度×0.10 + 实战项目密度×0.10。
    综合分 ≥ 0.65 进入推荐池；学习路径按质量分取 Top-3。
    """

    title: str = Field(description="课程名")
    platform: str = Field(description="课程平台")
    platform_score: float = Field(ge=0.0, le=1.0, description="平台权威性分")
    rating_score: float = Field(ge=0.0, le=1.0, description="用户评分分")
    enrollment_score: float = Field(ge=0.0, le=1.0, description="注册量与完成率分")
    recency_score: float = Field(ge=0.0, le=1.0, description="时效性分")
    skill_coverage_score: float = Field(ge=0.0, le=1.0, description="技能覆盖度分")
    project_density_score: float = Field(ge=0.0, le=1.0, description="实战项目密度分")
    quality_score: float = Field(ge=0.0, le=1.0, description="六维加权综合质量分")
    recommended: bool = Field(description="是否进入推荐池（≥0.65）")


class CrossValidationResult(BaseModel):
    """岗位组跨平台交叉验证结果（设计文档 §4.5）。

    聚合口径：`normalize_position_name(岗位名)` 归一的同岗位 JD 为一组。
    单源岗位：verified=False、confidence 低，入图谱需人工复核（附录：入图谱 ≥0.6）。
    """

    position_name: str = Field(description="归一化岗位名（组标识）")
    jd_count: int = Field(description="组内 JD 条数")
    source_count: int = Field(description="独立数据源数")
    sources: list[str] = Field(default_factory=list, description="来源列表（去重）")
    verified: bool = Field(description="≥2 独立源印证（技能级跨源验证通过）")
    confidence: float = Field(ge=0.0, le=1.0, description="跨源置信度（数据源数/一致性/时效加权）")
    verified_skill_ratio: float = Field(
        ge=0.0, le=1.0, description="≥2 源技能占比（跨源一致性）"
    )
    unverified_skills: list[str] = Field(
        default_factory=list, description="单源技能（未达 2 源印证，待人工审核）"
    )
    salary_median: float | None = Field(
        default=None, description="同岗位月薪中位数（元/月），不可解析为 None"
    )
    salary_outlier: bool = Field(
        default=False, description="多平台薪资中位数差异 >50%（跨货币口径见源码注释）"
    )
    experience_divergence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="经验要求跨平台分歧度（0-1，1=完全分歧）"
    )
