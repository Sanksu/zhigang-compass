"""动态演化模块。

设计文档 7.1 节：时间窗口演化算法（Z-score 统计 + MoM 辅助）+ 图谱版本管理。
"""

from app.services.evolution.schemas import (
    EvolutionSignal,
    GraphVersionMeta,
    SkillEvolutionTrend,
    SkillFrequencyWindow,
)
from app.services.evolution.detector import (
    EvolutionDetector,
    WindowProvider,
    Z_EMERGING,
    Z_RISING,
    Z_DECLINING,
    MIN_FREQ_FOR_ZSCORE,
    compute_zscore,
    classify_trend,
)
from app.services.evolution.graph_version import GraphVersionManager

__all__ = [
    "EvolutionDetector",
    "EvolutionSignal",
    "GraphVersionManager",
    "GraphVersionMeta",
    "MIN_FREQ_FOR_ZSCORE",
    "SkillEvolutionTrend",
    "SkillFrequencyWindow",
    "WindowProvider",
    "Z_DECLINING",
    "Z_EMERGING",
    "Z_RISING",
    "classify_trend",
    "compute_zscore",
]
