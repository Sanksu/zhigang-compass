"""时间窗口演化检测（设计文档 7.1 节）。

Z-score 为主判定信号：z = (f(t) - μ) / σ
- z > 2.0 → emerging
- z > 1.5 → rising
- z < -1.5 → declining
- 频次 < 10 → 小基数保护，不参与判定
"""

from app.services.evolution.schemas import (
    EvolutionSignal,
    SkillEvolutionTrend,
    SkillFrequencyWindow,
)

# Z-score 判定阈值（设计文档 7.1 节）
Z_EMERGING = 2.0
Z_RISING = 1.5
Z_DECLINING = -1.5

# 小基数保护阈值
MIN_FREQ_FOR_ZSCORE = 10


def compute_zscore(current: float, mean: float, std: float) -> float:
    """计算 Z-score：z = (f(t) - μ) / σ。

    std = 0 时返回 0.0（无波动无法判定异常）。
    """
    if std == 0:
        return 0.0
    return (current - mean) / std


def classify_trend(
    z_score: float,
    current_freq: int,
    protected: bool = False,
) -> SkillEvolutionTrend:
    """根据 Z-score 与频次判定趋势。

    protected=True 或频次 < MIN_FREQ_FOR_ZSCORE 时进入保护态。
    """
    if protected or current_freq < MIN_FREQ_FOR_ZSCORE:
        return SkillEvolutionTrend.PROTECTED
    if z_score > Z_EMERGING:
        return SkillEvolutionTrend.EMERGING
    if z_score > Z_RISING:
        return SkillEvolutionTrend.RISING
    if z_score < Z_DECLINING:
        return SkillEvolutionTrend.DECLINING
    return SkillEvolutionTrend.STABLE


class EvolutionDetector:
    """演化检测器接口。

    M3 实现：
    - 从 PostgreSQL 聚合 30 天滑动窗口频次
    - 计算 Z-score 与环比
    - 输出 EvolutionSignal 供图谱增量更新
    - 触发 graph_v{date}.json 快照生成（APOC）
    """

    def detect_skill(
        self,
        skill_id: str,
        current_window: SkillFrequencyWindow,
        historical_windows: list[SkillFrequencyWindow],
    ) -> EvolutionSignal:
        """检测单个技能的演化信号。

        Args:
            current_window: 当前 30 天窗口数据
            historical_windows: 历史窗口列表（≥2 个即可计算 Z-score）
        """
        raise NotImplementedError("演化检测实现将在 M3 由算法岗完成")

    def detect_batch(
        self,
        skill_ids: list[str],
    ) -> list[EvolutionSignal]:
        """批量检测技能演化信号（每日 05:00 定时任务调用）。"""
        raise NotImplementedError("批量演化检测将在 M3 由算法岗完成")
