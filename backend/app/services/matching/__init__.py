"""匹配引擎模块。

设计文档 9 节：基于内容的多维加权匹配 + Sentence-BERT 语义增强 + 规则/LLM 兜底。
"""

from app.services.matching.schemas import (
    CandidateProfile,
    CandidateSkill,
    MatchMode,
    MatchRequest,
    MatchResult,
    Necessity,
    PositionProfile,
    SkillRequirement,
)
from app.services.matching.engine import (
    MatchEngine,
    RuleBasedMatcher,
    apply_cii_correction,
    score_position,
)
from app.services.matching.semantic import SemanticUnavailableError, SkillEmbedder
from app.services.matching.weights import load_weights, DEFAULT_WEIGHTS, load_sim_threshold

__all__ = [
    "CandidateProfile",
    "CandidateSkill",
    "MatchEngine",
    "MatchMode",
    "MatchRequest",
    "MatchResult",
    "Necessity",
    "PositionProfile",
    "RuleBasedMatcher",
    "SemanticUnavailableError",
    "SkillEmbedder",
    "SkillRequirement",
    "DEFAULT_WEIGHTS",
    "apply_cii_correction",
    "load_sim_threshold",
    "load_weights",
    "score_position",
]
