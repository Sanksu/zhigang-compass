"""诊断报告数据模型（设计文档 §9.5 节）。

LLM 将匹配结果 + 差距 + 学习路径作为 context 生成结构化诊断报告：
① 总体匹配度解读 ② 三维雷达图解读 ③ 关键差距 Top-5（附证据）④ 学习路径解读 ⑤ 改进建议。
每条差距断言附带 evidence_id（来源 URL / 源名）可点击追溯（§9.5 要求 evidence_id 覆盖率 100%）。
"""

from pydantic import BaseModel, Field


class GapAdvice(BaseModel):
    """单条关键差距的诊断建议。"""

    skill: str = Field(description="技能名")
    advice: str = Field(description="改进建议")
    evidence_id: str = Field(
        default="", description="证据引用（来源 URL 或源名），前端可点击追溯"
    )


class DiagnosisReport(BaseModel):
    """人岗比对诊断报告。"""

    overall_summary: str = Field(description="总体匹配度解读（含总体匹配度结论）")
    radar_analysis: str = Field(description="三维雷达图解读（必备/加分/经验各维度强弱）")
    top_gaps: list[GapAdvice] = Field(default_factory=list, description="关键差距 Top-5 及改进建议")
    path_analysis: str = Field(default="", description="学习路径解读（合理性 + 预计投入）")
    recommendations: list[str] = Field(default_factory=list, description="整体改进建议清单")
