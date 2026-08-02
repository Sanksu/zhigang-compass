"""演化信号服务测试（设计文档 §7.1：快照序列 → 频次窗口 → Z-score 信号）。"""

from app.services.evolution.schemas import SkillEvolutionTrend
from app.services.evolution.trend_service import (
    detect_signals_from_snapshots,
    rank_signals,
)


def _snapshot(version_id: str, skill_freqs: dict[str, int]) -> dict:
    """构造单期快照：给定技能 ID → 频次，生成 nodes + REQUIRES 边。"""
    nodes, edges = [], []
    for sid, count in skill_freqs.items():
        nodes.append({"id": sid, "name": f"技能{sid}", "type": "skill"})
        for i in range(count):
            pid = f"pos_{sid}_{i}"
            nodes.append({"id": pid, "name": f"岗位{i}", "type": "position"})
            edges.append({"source": pid, "target": sid})
    return {"version_id": version_id, "nodes": nodes, "edges": edges}


def test_detect_signals_with_emerging_trend():
    """技能频次显著上升 → emerging（z > 2.0）。"""
    snapshots = [
        _snapshot("v1", {"sk_a": 10, "sk_b": 20}),
        _snapshot("v2", {"sk_a": 12, "sk_b": 20}),
        _snapshot("v3", {"sk_a": 40, "sk_b": 20}),  # sk_a 突破均值 → emerging
    ]
    signals = detect_signals_from_snapshots(snapshots)
    by_id = {s.skill_id: s for s in signals}
    assert by_id["sk_a"].trend == SkillEvolutionTrend.EMERGING
    assert by_id["sk_a"].z_score is not None and by_id["sk_a"].z_score > 2.0
    # sk_b 无波动 → stable
    assert by_id["sk_b"].trend == SkillEvolutionTrend.STABLE


def test_detect_signals_with_declining_trend():
    """技能频次显著下降 → declining（z < -1.5）。"""
    snapshots = [
        _snapshot("v1", {"sk_a": 30}),
        _snapshot("v2", {"sk_a": 28}),
        _snapshot("v3", {"sk_a": 12}),  # 降至 ≥10（避开小基数保护）
    ]
    signals = detect_signals_from_snapshots(snapshots)
    assert signals[0].trend == SkillEvolutionTrend.DECLINING
    assert signals[0].z_score < -1.5


def test_insufficient_snapshots_returns_empty():
    """少于 2 期快照（冷启动）不判定，返回空信号。"""
    assert detect_signals_from_snapshots([]) == []
    assert detect_signals_from_snapshots([_snapshot("v1", {"sk_a": 10})]) == []


def test_rank_signals_filters_and_sorts():
    """按趋势过滤 + confidence 降序取 Top-N。"""
    snapshots = [
        _snapshot("v1", {"sk_a": 10, "sk_b": 20, "sk_c": 50}),
        _snapshot("v2", {"sk_a": 12, "sk_b": 20, "sk_c": 45}),
        _snapshot("v3", {"sk_a": 40, "sk_b": 20, "sk_c": 12}),  # sk_c 降至 ≥10（避开小基数保护）
    ]
    signals = detect_signals_from_snapshots(snapshots)
    emerging = rank_signals(signals, "emerging", top_n=10)
    declining = rank_signals(signals, "declining", top_n=10)
    # sk_a 上升（emerging），sk_c 下降（declining），sk_b 平稳（stable）
    assert [s.skill_id for s in emerging] == ["sk_a"]
    assert [s.skill_id for s in declining] == ["sk_c"]
    # confidence 降序
    confs = [s.confidence for s in emerging]
    assert confs == sorted(confs, reverse=True)


def test_rank_signals_respects_top_n():
    snapshots = [
        _snapshot("v1", {"sk_a": 10, "sk_b": 20}),
        _snapshot("v2", {"sk_a": 12, "sk_b": 20}),
        _snapshot("v3", {"sk_a": 30, "sk_b": 40}),  # 两者均上升，top_n=1 只取 confidence 最高
    ]
    signals = detect_signals_from_snapshots(snapshots)
    assert len(rank_signals(signals, "emerging", top_n=1)) == 1
