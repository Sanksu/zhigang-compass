"""新岗位发现检测器（设计文档 7.2.3 节判定流程）。

单阶段判定：Z-score 统计门控 + RAG 接地。
阶段一（Z-score 门控，仅 JD 源驱动）：
  IF z_score > 2.0 AND source_diversity ≥ 2 AND jd_freq_ma3 ≥ 10 → candidate
  ELIF z_score > 1.5 AND source_diversity ≥ 2 → candidate（保守观察）
  ELSE → 继续监测
冷启动（历史 < 60 天）：wilson_lower > 0.3 AND source_diversity ≥ 3 → candidate
阶段二（RAG 接地 + 种子列表，仅对 candidate 触发，M4 开放自动 emerging 判定）
"""

from app.services.discovery.schemas import CandidatePosition, DiscoveryFeatures
from app.services.discovery.confidence import wilson_lower, WILSON_COLD_START_THRESHOLD

# Z-score 门控阈值
Z_SCORE_STRICT = 2.0       # 严格门控
Z_SCORE_CONSERVATIVE = 1.5  # 保守观察
MIN_SOURCE_DIVERSITY = 2
MIN_JD_FREQ_MA3 = 10
MIN_SOURCE_COLD_START = 3


def passes_gate(features: DiscoveryFeatures, history_days: int) -> bool:
    """判定特征是否通过 candidate 门控。

    Args:
        features: 4 核心特征
        history_days: 历史窗口天数（< 60 走冷启动 Wilson score 兜底）

    Returns:
        True 表示进入 candidate 池
    """
    if features.z_score is None:
        return False

    # 冷启动：历史不足 60 天，用 Wilson score 兜底
    if history_days < 60:
        # successes = 当前窗口频次，total 保守用历史窗口总数
        # 此处仅门控判定，具体 successes/total 由实现层传入
        return False  # Wilson score 计算需要实现层提供 successes/total

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
    """新岗位发现检测器接口。

    M3 实现：
    - 每日定时任务扫描技能频次异常
    - 阶段一 Z-score 门控触发 candidate
    - 阶段二 RAG 接地（M4 开放）+ 种子列表匹配
    - candidate 写入 Neo4j + 审核队列
    """

    def detect_candidates(self) -> list[CandidatePosition]:
        """扫描全量技能，输出新进入 candidate 池的岗位列表。"""
        raise NotImplementedError("新岗位发现实现将在 M3 由算法岗完成")

    def ground_with_rag(
        self,
        candidate: CandidatePosition,
    ) -> CandidatePosition:
        """RAG 接地：检索权威岗位库（人社部 + LinkedIn + O*NET）+ 种子列表匹配。

        M4 开放自动 emerging 判定，M3 影子模式仅人工触发。
        """
        raise NotImplementedError("RAG 接地实现将在 M4 由算法岗完成")
