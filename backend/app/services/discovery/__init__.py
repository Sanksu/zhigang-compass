"""新岗位发现模块。

设计文档 7.2 节：六状态机 + Z-score 门控 + RAG 接地 + 置信度计算。
分层源策略：JD 源驱动 candidate 触发，arXiv/GitHub/SO 进观察池作置信度加分。
"""

from app.services.discovery.schemas import (
    CandidatePosition,
    ConfidenceScore,
    DiscoveryFeatures,
    PositionState,
)
from app.services.discovery.confidence import (
    compute_confidence,
    wilson_lower,
    WILSON_COLD_START_THRESHOLD,
)
from app.services.discovery.detector import (
    DiscoveryDetector,
    MIN_SOURCE_COLD_START,
    Z_SCORE_STRICT,
    Z_SCORE_CONSERVATIVE,
    passes_gate,
    passes_cold_start_gate,
)
from app.services.discovery.state_machine import (
    PositionStateMachine,
    VALID_TRANSITIONS,
)

__all__ = [
    "CandidatePosition",
    "ConfidenceScore",
    "DiscoveryDetector",
    "DiscoveryFeatures",
    "MIN_SOURCE_COLD_START",
    "PositionState",
    "PositionStateMachine",
    "VALID_TRANSITIONS",
    "WILSON_COLD_START_THRESHOLD",
    "Z_SCORE_CONSERVATIVE",
    "Z_SCORE_STRICT",
    "compute_confidence",
    "passes_cold_start_gate",
    "passes_gate",
    "wilson_lower",
]
