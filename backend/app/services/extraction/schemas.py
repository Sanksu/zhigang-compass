"""抽取结果的数据模型（Pydantic，兼作 LLM JSON Schema 约束）。

实体类型映射见设计文档 5.1 节。每个模型的 `model_config` 中 `json_schema_extra`
可作为 LLM Few-Shot 的 schema 约束（幻觉防控第一道防线）。
"""

from pydantic import BaseModel, Field
from typing import Optional


class SkillExtracted(BaseModel):
    """从文本中提取的技能。"""
    name: str = Field(description="技能名称")
    category: Optional[str] = Field(default=None, description='技能分类，如"编程语言"、"框架"、"工具"')
    description: Optional[str] = Field(default=None, description="简短描述/上下文")


class ToolExtracted(BaseModel):
    """从文本中提取的工具/框架。"""
    name: str = Field(description="工具/框架名称")
    category: Optional[str] = Field(default=None, description="工具类别")
    vendor: Optional[str] = Field(default=None, description="供应商/组织")


class EducationExtracted(BaseModel):
    """教育要求。"""
    level: Optional[str] = Field(default=None, description="学历要求：大专/本科/硕士/博士")
    major: Optional[str] = Field(default=None, description="专业要求")


class CertificationExtracted(BaseModel):
    """证书要求。"""
    name: str = Field(description="证书/认证名称")


class REQUIRESRelation(BaseModel):
    """岗位-技能关系。"""
    skill_name: str = Field(description="技能名称")
    necessity: str = Field(description="必要性: must (必备) 或 nice (加分)", pattern="^(must|nice)$")
    level: Optional[str] = Field(default=None, description="熟练度：初级/中级/高级/专家")


class JDExtractionResult(BaseModel):
    """JD 抽取的完整结果。"""
    position_name: str = Field(description="岗位名称")
    level: Optional[str] = Field(default=None, description='岗位级别，如"初级"、"高级"、"资深"')
    industry: Optional[str] = Field(default=None, description="行业")
    salary_range: Optional[str] = Field(default=None, description="薪资范围")
    skills: list[SkillExtracted] = Field(default_factory=list, description="技能列表")
    tools: list[ToolExtracted] = Field(default_factory=list, description="工具列表")
    education: Optional[EducationExtracted] = Field(default=None, description="教育要求")
    certifications: list[CertificationExtracted] = Field(default_factory=list, description="证书要求")
    requirements: list[REQUIRESRelation] = Field(default_factory=list, description="岗位→技能要求关系")
