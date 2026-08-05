"""简历抽取的数据模型（Pydantic，兼作 LLM JSON Schema 约束）。

对齐设计文档 §8.3 抽取字段与 matching/schemas.py CandidateProfile 画像：
- 文本已先经 PII 脱敏（pii_mask.py），name/phone/email 在抽取时呈
  [NAME]/[PHONE]/[EMAIL] 占位符；LLM 抽取完成后由 tasks.resume_parse 经
  restore_pii 映射表回填为原始值（设计文档 §8.2），parsed_data 落库含真实值。
- parsed_data 落库形态为 model_dump()，供 match.py `_build_candidate` 构建画像。
"""

from typing import Optional

from pydantic import BaseModel, Field


class ResumeEducation(BaseModel):
    """教育背景。"""
    school: str = Field(default="", description="学校名称")
    major: str = Field(default="", description="专业")
    level: str = Field(default="", description="学历：大专/本科/硕士/博士")
    start: str = Field(default="", description="开始时间")
    end: str = Field(default="", description="结束时间")


class ResumeWorkExperience(BaseModel):
    """工作经历。"""
    company: str = Field(default="", description="公司名称")
    position: str = Field(default="", description="职位")
    start: str = Field(default="", description="开始时间")
    end: str = Field(default="", description="结束时间")
    description: str = Field(default="", description="职责描述")


class ResumeProject(BaseModel):
    """项目经验。"""
    name: str = Field(description="项目名称")
    role: str = Field(default="", description="担任角色")
    stack: list[str] = Field(default_factory=list, description="技术栈")
    description: str = Field(default="", description="项目描述")


class ResumeSkill(BaseModel):
    """候选人技能。"""
    name: str = Field(description="技能名称")
    proficiency: int = Field(default=2, ge=1, le=3, description="熟练度：1 了解 / 2 熟悉 / 3 精通")
    low_confidence: bool = Field(default=False, description="LLM 推断（非明确出现）标记")
    unmapped: bool = Field(
        default=False,
        description="未命中标准技能白名单（保留待人工确认，设计文档 8.4 节）",
    )


class ResumeExtractionResult(BaseModel):
    """简历抽取的完整结果。"""
    name: str = Field(default="", description="姓名（脱敏占位符 [NAME]）")
    phone: str = Field(default="", description="电话（脱敏占位符 [PHONE]）")
    email: str = Field(default="", description="邮箱（脱敏占位符 [EMAIL]）")
    total_years: float = Field(default=0.0, description="总工作年限，无法推断为 0")
    education_level: str = Field(default="", description="最高学历：大专/本科/硕士/博士")
    school_tier: Optional[str] = Field(default=None, description="学校层级：985/211/普通/海外")
    education: list[ResumeEducation] = Field(default_factory=list, description="教育背景")
    work_experience: list[ResumeWorkExperience] = Field(default_factory=list, description="工作经历")
    skills: list[ResumeSkill] = Field(default_factory=list, description="技能列表")
    soft_skills: list[str] = Field(
        default_factory=list,
        description="软技能（LLM 从项目角色/经历推断，仅限岗位本体 20 项白名单；"
        "并入 skills 时标记 low_confidence，匹配降权 ×0.5，设计文档 9.2 节）",
    )
    projects: list[ResumeProject] = Field(default_factory=list, description="项目经验")
    certifications: list[str] = Field(default_factory=list, description="证书")
    domain_experience: list[str] = Field(default_factory=list, description="领域经验（如金融/电商/自动驾驶）")
