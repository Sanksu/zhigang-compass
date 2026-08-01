"""新岗位发现检测器（设计文档 7.2.3 节判定流程）。

单阶段判定：Z-score 统计门控 + RAG 接地。
阶段一（Z-score 门控，仅 JD 源驱动）：
  IF z_score > 2.0 AND source_diversity ≥ 2 AND jd_freq_ma3 ≥ 10 → candidate
  ELIF z_score > 1.5 AND source_diversity ≥ 2 → candidate（保守观察）
  ELSE → 继续监测
冷启动（历史 < 60 天）：wilson_lower > 0.3 AND source_diversity ≥ 3 → candidate
阶段二（RAG 接地 + 种子列表，M4 开放自动 emerging 判定，M3 影子模式人工触发）
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Protocol
from uuid import uuid4

from app.services.discovery.schemas import (
    CandidatePosition,
    DiscoveryFeatures,
    PositionState,
)
from app.services.discovery.confidence import (
    wilson_lower,
    WILSON_COLD_START_THRESHOLD,
)

# Z-score 门控阈值
Z_SCORE_STRICT = 2.0       # 严格门控
Z_SCORE_CONSERVATIVE = 1.5  # 保守观察
MIN_SOURCE_DIVERSITY = 2
MIN_JD_FREQ_MA3 = 10
MIN_SOURCE_COLD_START = 3

# 冷启动窗口阈值（设计文档 7.2.3 节）
COLD_START_DAYS = 60

# 项目统一时区 UTC+8
_TZ_CN = timezone(timedelta(hours=8))


@dataclass
class DiscoveryInput:
    """单个技能的发现判定输入。

    cold_successes/cold_total 仅冷启动（history_days < COLD_START_DAYS）时使用，
    为 Wilson score 兜底提供二项分布样本；正常窗口无需提供。
    """

    position_name: str
    features: DiscoveryFeatures
    history_days: int
    cold_successes: Optional[int] = None
    cold_total: Optional[int] = None


class CandidateProvider(Protocol):
    """候选输入数据源（M3 由 PostgreSQL 聚合层实现）。"""

    def iter_inputs(self) -> Iterable[DiscoveryInput]:
        """遍历全部待判定技能。"""
        ...


def passes_gate(features: DiscoveryFeatures, history_days: int) -> bool:
    """判定特征是否通过 candidate 门控。

    Args:
        features: 4 核心特征
        history_days: 历史窗口天数（< COLD_START_DAYS 走冷启动 Wilson score 兜底）

    Returns:
        True 表示进入 candidate 池
    """
    if features.z_score is None:
        return False

    # 冷启动：历史不足 60 天，改用 Wilson score 兜底（passes_cold_start_gate）
    if history_days < COLD_START_DAYS:
        return False

    # 正常判定：Z-score 门控
    if features.z_score > Z_SCORE_STRICT:
        return (
            features.source_diversity >= MIN_SOURCE_DIVERSITY
            and features.jd_freq_ma3 >= MIN_JD_FREQ_MA3
        )
    if features.z_score > Z_SCORE_CONSERVATIVE:
        return features.source_diversity >= MIN_SOURCE_DIVERSITY
    return False


def passes_cold_start_gate(
    successes: int,
    total: int,
    source_diversity: int,
) -> bool:
    """冷启动 Wilson score 兜底门控。"""
    if source_diversity < MIN_SOURCE_COLD_START:
        return False
    return wilson_lower(successes, total) > WILSON_COLD_START_THRESHOLD


class DiscoveryDetector:
    """新岗位发现检测器。

    阶段一已实现（Z-score/Wilson 门控 + candidate 组装）；
    阶段二 RAG 接地为 M4 交付项，M3 影子模式仅人工触发（ground_with_rag 保持 stub）。
    """

    def detect_candidates(self, provider: CandidateProvider) -> list[CandidatePosition]:
        """扫描全量技能，输出新进入 candidate 池的岗位列表。

        Args:
            provider: 特征数据源，逐技能提供门控所需输入

        Returns:
            通过阶段一门控的 CandidatePosition 列表（state=candidate）
        """
        candidates: list[CandidatePosition] = []
        for inp in provider.iter_inputs():
            if self._passes(inp):
                candidates.append(self._build_candidate(inp))
        return candidates

    def _passes(self, inp: DiscoveryInput) -> bool:
        """阶段一门控：正常 Z-score 门控或冷启动 Wilson 兜底。"""
        if passes_gate(inp.features, inp.history_days):
            return True
        if (
            inp.history_days < COLD_START_DAYS
            and inp.cold_successes is not None
            and inp.cold_total is not None
        ):
            return passes_cold_start_gate(
                inp.cold_successes, inp.cold_total, inp.features.source_diversity
            )
        return False

    @staticmethod
    def _build_candidate(inp: DiscoveryInput) -> CandidatePosition:
        return CandidatePosition(
            candidate_id=f"cand-{uuid4().hex[:12]}",
            position_name=inp.position_name,
            state=PositionState.CANDIDATE,
            features=inp.features,
            detected_at=datetime.now(_TZ_CN).isoformat(timespec="seconds"),
        )

    def ground_with_rag(
        self,
        candidate: CandidatePosition,
    ) -> CandidatePosition:
        """RAG 接地：检索权威岗位库（人社部 + LinkedIn + O*NET）+ 种子列表匹配。

        M4 开放自动 emerging 判定，M3 影子模式仅人工触发。
        """
        raise NotImplementedError("RAG 接地实现将在 M4 由算法岗完成")
