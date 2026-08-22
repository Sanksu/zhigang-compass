"""演化信号服务（设计文档 §7.1 时间窗口演化检测接入真实快照）。

从 graph_versions 快照序列（T+1 每日全量快照）重建技能频次窗口：
每个快照中该技能被多少岗位 REQUIRES（edges.target 计数）即为一期频次采样，
按时间升序构成窗口序列 → EvolutionDetector 计算 Z-score 信号。

数据不足（快照 < 2 期）时返回空信号（不武断判定），前端显示说明。
"""

from app.services.evolution.detector import EvolutionDetector
from app.services.evolution.schemas import EvolutionSignal, SkillFrequencyWindow


def _skill_name_by_id(snapshot: dict) -> dict[str, str]:
    """快照 nodes 中的技能 ID → 技能名映射。"""
    return {
        n.get("id", ""): n.get("name", n.get("id", ""))
        for n in snapshot.get("nodes", [])
        if n.get("type") == "skill" or str(n.get("id", "")).startswith("sk_")
    }


def _skill_freq_windows(snapshots: list[dict]) -> dict[str, list[SkillFrequencyWindow]]:
    """按技能聚合频次窗口序列（时间升序）。

    每期快照：该技能作为 REQUIRES 目标边（target=技能）的计数即当期频次；
    快照创建时间不可用时用序号占位（仅影响展示，不影响 Z-score）。

    分子与分母同边集（均仅 REQUIRES）：BELONGS_TO/ALTERNATIVE_OF 等
    技能→技能边的 target 同样是 sk_ 前缀，混入分子会令占比可 >1 且口径
    与 state_machine 的 REQUIRES 过滤约定相悖（评审 A-1 负责人拍板①，
    与桑基 P1-2 同根）。旧快照边无 relation 标注则整体按历史口径兼容：
    分子全计、分母记 0（由 detector 整序列退回计数口径）。
    """
    windows: dict[str, list[SkillFrequencyWindow]] = {}
    for snap in snapshots:
        names = _skill_name_by_id(snap)
        edges = snap.get("edges", [])
        # 快照级判定是否携带关系标注（新旧快照形态不混布于同一版本）
        has_relations = any(e.get("relation") for e in edges)
        # 归一化分母：当期 REQUIRES 总边数（占比口径，抗采集总量波动）
        total_requires = (
            sum(1 for e in edges if e.get("relation") == "REQUIRES")
            if has_relations
            else 0
        )
        freq: dict[str, int] = {}
        for e in edges:
            target = e.get("target")
            if not str(target).startswith("sk_"):
                continue
            if has_relations and e.get("relation") != "REQUIRES":
                continue
            freq[target] = freq.get(target, 0) + 1
        for skill_id, count in freq.items():
            windows.setdefault(skill_id, []).append(
                SkillFrequencyWindow(
                    skill_id=skill_id,
                    skill_name=names.get(skill_id, skill_id),
                    window_start=snap.get("version_id", ""),
                    window_end=snap.get("version_id", ""),
                    frequency=count,
                    total_requires=total_requires,
                )
            )
    return windows


def detect_signals_from_snapshots(
    snapshots: list[dict],
    degraded_flags: list[bool] | None = None,
) -> list[EvolutionSignal]:
    """从快照序列检测全部技能演化信号（Emerging/Declining/Rising/Stable/Protected）。

    degraded_flags 与 snapshots 等长对齐：命中 data_warning（证据量较上期
    萎缩 <50% / 膨胀 >200%）的快照整期剔除——部分采集源故障时总量骤变会
    反向放大其余技能占比产生伪 emerging，此类窗口既不作为 current 也不进入
    μ/σ（评审 A-3 负责人拍板①；端点侧「打标不剔除」注解层不受影响）。
    剔除后不足 2 期返回空（数据不足不武断判定）。
    """
    if degraded_flags is not None:
        snapshots = [s for s, bad in zip(snapshots, degraded_flags) if not bad]
    if len(snapshots) < 2:
        return []

    windows = _skill_freq_windows(snapshots)
    detector = EvolutionDetector()
    signals: list[EvolutionSignal] = []
    for skill_id, seq in windows.items():
        current = seq[-1]
        historical = seq[:-1]
        signals.append(detector.detect_skill(skill_id, current, historical))
    return signals


def rank_signals(
    signals: list[EvolutionSignal],
    trend: str,
    top_n: int = 10,
) -> list[EvolutionSignal]:
    """按趋势过滤并按 confidence 降序、|z| 强度二级降序取 Top-N。

    trend: "emerging"（z > 2.0）或 "declining"（z < -1.5）。

    二级排序原因：confidence = min(|z|/4, 1)，|z|≥4 时全部饱和为 1.0，
    若只按 confidence 排序会退化为稳定输入序，Top-N 可能漏掉最强信号。
    """
    matches = [s for s in signals if s.trend.value == trend]
    matches.sort(
        key=lambda s: (
            s.confidence,
            abs(s.z_score) if s.z_score is not None else -1.0,
        ),
        reverse=True,
    )
    return matches[:top_n]
