"""演化算法数据模型（设计文档 7.1 节时间窗口演化算法）。

Z-score 统计为主判定信号，环比（MoM）辅助。
频次 < 10 的技能受小基数保护不参与判定，改用 Wilson score 兜底（见 discovery 模块）。
"""

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class SkillEvolutionTrend(str, Enum):
    """技能演化趋势（Z-score 阈值映射）。"""
    EMERGING = "emerging"    # z > 2.0
    RISING = "rising"        # z > 1.5
    STABLE = "stable"        # -1.5 ≤ z ≤ 1.5
    DECLINING = "declining"  # z < -1.5
    PROTECTED = "protected"  # 频次 < 10，小基数保护，不参与判定


class SkillFrequencyWindow(BaseModel):
    """技能频次窗口数据。"""
    skill_id: str
    skill_name: str
    window_start: str = Field(description="窗口起始日期 ISO8601")
    window_end: str = Field(description="窗口结束日期 ISO8601")
    frequency: int = Field(description="窗口内 JD 出现频次")
    source_count: int = Field(default=1, description="独立 JD 源数")


class EvolutionSignal(BaseModel):
    """单个技能的演化信号输出。"""
    skill_id: str
    skill_name: str
    z_score: Optional[float] = Field(default=None, description="标准分，小基数保护时为 None")
    mom_growth: Optional[float] = Field(default=None, description="环比增长率")
    current_freq: int
    historical_mean: Optional[float] = Field(default=None, description="历史窗口均值 μ")
    historical_std: Optional[float] = Field(default=None, description="历史窗口标准差 σ")
    trend: SkillEvolutionTrend
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list, description="证据 JD 的 evidence_id 列表")


class GraphVersionMeta(BaseModel):
    """图谱版本元数据（设计文档 7.1 节版本管理）。"""
    version_id: str
    created_at: str
    change_summary: str = Field(description="本次变更摘要")
    triggered_by: str = Field(default="scheduled", description="触发方式：scheduled/manual")
    node_added: int = 0
    node_removed: int = 0
    node_changed: int = 0
