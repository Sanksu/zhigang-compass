"""时间窗口演化检测（设计文档 7.1 节）。

Z-score 为主判定信号：z = (f(t) - μ) / σ
- z > 2.0 → emerging
- z > 1.5 → rising
- z < -1.5 → declining
- 频次 < 10 → 小基数保护，不参与判定
"""

import statistics
from typing import Protocol

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


def _window_value(w: SkillFrequencyWindow) -> float:
    """窗口参与 Z-score 的数值：占比口径优先，抗采集总量波动。

    total_requires > 0 时返回 frequency/total_requires（该技能在当期全部
    岗位-技能关系中的占比）——爬虫宕机导致全技能频次齐跌时占比不变，
    不再批量误判 declining；分母未知（0）时退回原始计数（兼容旧数据）。
    """
    if w.total_requires > 0:
        return w.frequency / w.total_requires
    return float(w.frequency)


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


def _confidence_from_zscore(z: float | None) -> float:
    """Z-score 强度 → 置信度映射（参考实现）。

    设计文档未规定演化信号置信度公式，采用 |z|/4 单调映射：
    |z|=2（emerging 阈值）→ 0.5，|z|=4 → 1.0。M5 可由校准模型替换。
    """
    if z is None:
        return 0.0
    return min(abs(z) / 4.0, 1.0)


def _mom_growth(current: int, previous: int) -> float | None:
    """环比增长率（MoM）：分母为上一窗口频次，上窗口为 0 时无法计算。"""
    if previous == 0:
        return None
    return (current - previous) / previous


class WindowProvider(Protocol):
    """技能频次窗口数据源（M3 由 PostgreSQL 聚合层实现）。"""

    def get_windows(
        self, skill_id: str
    ) -> tuple[SkillFrequencyWindow, list[SkillFrequencyWindow]]:
        """返回 (当前窗口, 历史窗口列表)。"""
        ...


class EvolutionDetector:
    """演化检测器。

    纯计算实现：Z-score + MoM 双信号 + 小基数保护。
    窗口数据由外部 WindowProvider 提供，detect_batch 仅做批量编排。
    """

    def detect_skill(
        self,
        skill_id: str,
        current_window: SkillFrequencyWindow,
        historical_windows: list[SkillFrequencyWindow],
    ) -> EvolutionSignal:
        """检测单个技能的演化信号。

        Args:
            skill_id: 标准技能 ID
            current_window: 当前 30 天窗口数据
            historical_windows: 历史窗口列表（≥1 个即可计算 Z-score 与环比）
        """
        freqs = [w.frequency for w in historical_windows]
        # Z-score 序列用占比口径（total_requires>0 时），输出字段仍用原始计数
        hist_values = [_window_value(w) for w in historical_windows]

        if not freqs:
            # 无历史窗口：无法计算 Z-score/MoM，保守标记为稳定观察基线
            return EvolutionSignal(
                skill_id=skill_id,
                skill_name=current_window.skill_name,
                z_score=None,
                mom_growth=None,
                current_freq=current_window.frequency,
                historical_mean=None,
                historical_std=None,
                trend=SkillEvolutionTrend.STABLE,
                confidence=0.0,
            )

        mean = sum(hist_values) / len(hist_values)
        std = statistics.stdev(hist_values) if len(hist_values) > 1 else 0.0
        z = compute_zscore(_window_value(current_window), mean, std)
        trend = classify_trend(z, current_window.frequency)

        # 小基数保护：z 不对外输出（schemas 约定保护态 z=None）
        output_z = z if trend != SkillEvolutionTrend.PROTECTED else None
        mom = _mom_growth(current_window.frequency, freqs[-1])

        return EvolutionSignal(
            skill_id=skill_id,
            skill_name=current_window.skill_name,
            z_score=round(output_z, 4) if output_z is not None else None,
            mom_growth=round(mom, 4) if mom is not None else None,
            current_freq=current_window.frequency,
            historical_mean=round(mean, 4),
            historical_std=round(std, 4),
            trend=trend,
            confidence=round(_confidence_from_zscore(output_z), 4),
        )

    def detect_batch(
        self,
        skill_ids: list[str],
        window_provider: WindowProvider,
    ) -> list[EvolutionSignal]:
        """批量检测技能演化信号（每日 05:00 定时任务调用）。

        Args:
            skill_ids: 需检测的技能 ID 列表
            window_provider: 窗口数据源（M3 接入 PostgreSQL 聚合层）

        Raises:
            ValueError: window_provider 缺失时立即失败，不静默返回空结果
        """
        if window_provider is None:
            raise ValueError("detect_batch 必须提供 window_provider 数据源")
        return [
            self.detect_skill(sid, *window_provider.get_windows(sid))
            for sid in skill_ids
        ]
