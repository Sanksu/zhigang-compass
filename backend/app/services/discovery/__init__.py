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
    CandidateProvider,
    DiscoveryDetector,
    DiscoveryInput,
    MIN_SOURCE_COLD_START,
    Z_SCORE_STRICT,
    Z_SCORE_CONSERVATIVE,
    passes_gate,
    passes_cold_start_gate,
)
from app.services.discovery.state_machine import (
    WindowFreq,
    can_promote_to_emerging,
    decline_rate,
    evaluate_auto_transition,
    evaluate_active_decline,
    has_recovery,
    window_volatility,
    PositionStateMachine,
    VALID_TRANSITIONS,
)
from app.services.discovery.grounding import (
    RagGroundingResult,
    ground_with_rag,
    match_seed,
    search_authoritative,
)

__all__ = [
    "CandidatePosition",
    "CandidateProvider",
    "ConfidenceScore",
    "DiscoveryDetector",
    "DiscoveryFeatures",
    "DiscoveryInput",
    "MIN_SOURCE_COLD_START",
    "PositionState",
    "PositionStateMachine",
    "RagGroundingResult",
    "VALID_TRANSITIONS",
    "WILSON_COLD_START_THRESHOLD",
    "WindowFreq",
    "Z_SCORE_CONSERVATIVE",
    "Z_SCORE_STRICT",
    "can_promote_to_emerging",
    "compute_confidence",
    "decline_rate",
    "evaluate_auto_transition",
    "evaluate_active_decline",
    "ground_with_rag",
    "has_recovery",
    "match_seed",
    "passes_cold_start_gate",
    "passes_gate",
    "search_authoritative",
    "wilson_lower",
    "window_volatility",
]
