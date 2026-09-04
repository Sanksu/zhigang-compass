"""演化信号服务测试（设计文档 §7.1：快照序列 → 频次窗口 → Z-score 信号）。"""

from app.services.evolution.schemas import SkillEvolutionTrend
from app.services.evolution.trend_service import (
    _skill_freq_windows,
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


def test_rank_signals_uses_abs_z_as_secondary_key():
    """confidence 饱和（|z|≥4 → 1.0）时，按 |z| 强度二级排序（回归：Top-N 曾退化为稳定序）。"""
    snapshots = [
        _snapshot("v1", {"sk_x": 50, "sk_y": 10}),
        _snapshot("v2", {"sk_x": 52, "sk_y": 12}),
        _snapshot("v3", {"sk_x": 70, "sk_y": 60}),
    ]
    signals = detect_signals_from_snapshots(snapshots)
    by_id = {s.skill_id: s for s in signals}
    # 两者均为 emerging 且 confidence 饱和 1.0（|z| ≥ 4）
    assert by_id["sk_x"].confidence == 1.0
    assert by_id["sk_y"].confidence == 1.0
    assert abs(by_id["sk_y"].z_score) > abs(by_id["sk_x"].z_score)
    # 修复前：confidence 并列时稳定排序保留输入序（sk_x 在前）；修复后：|z| 强者在前
    emerging = rank_signals(signals, "emerging", top_n=10)
    assert [s.skill_id for s in emerging][0] == "sk_y"


def test_windows_zero_filled_across_all_snapshots():
    """零填充（第八轮 P1-6）：出现过的技能每期都有窗口，消失期 freq=0。"""
    snapshots = [
        _snapshot("v1", {"sk_a": 20, "sk_b": 5}),
        _snapshot("v2", {"sk_a": 18}),  # sk_b 本期消失
        _snapshot("v3", {"sk_a": 22}),  # sk_b 持续消失
    ]
    windows = _skill_freq_windows(snapshots)
    assert [w.frequency for w in windows["sk_a"]] == [20, 18, 22]
    assert [w.frequency for w in windows["sk_b"]] == [5, 0, 0]
    assert [w.window_start for w in windows["sk_b"]] == ["v1", "v2", "v3"]


def test_disappeared_skill_current_reflects_latest_snapshot():
    """技能最新期消失：current 取真实最新期（freq=0 落小基数保护），不再拿最后出现期伪装在场。"""
    snapshots = [
        _snapshot("v1", {"sk_old": 30, "sk_stable": 20}),
        _snapshot("v2", {"sk_old": 28, "sk_stable": 20}),
        _snapshot("v3", {"sk_stable": 20}),  # sk_old 彻底消失
    ]
    signals = detect_signals_from_snapshots(snapshots)
    by_id = {s.skill_id: s for s in signals}
    # 修复前：current=28（最后出现期），伪 stable；修复后：current_freq=0，小基数门 → PROTECTED
    assert by_id["sk_old"].current_freq == 0
    assert by_id["sk_old"].trend == SkillEvolutionTrend.PROTECTED
    assert by_id["sk_old"].z_score is None
    # 在场技能不受影响
    assert by_id["sk_stable"].trend == SkillEvolutionTrend.STABLE


def test_mid_gap_zero_counts_into_mean_std():
    """中途断档：零值如实计入 μ/σ（序列 [40,0,40,40,12] → z=-0.9 stable），不在断续子序列上放大 z。"""
    snapshots = [
        _snapshot("v1", {"sk_g": 40}),
        _snapshot("v2", {}),  # 断档期（快照本身无该技能边）
        _snapshot("v3", {"sk_g": 40}),
        _snapshot("v4", {"sk_g": 40}),
        _snapshot("v5", {"sk_g": 12}),  # 回落但仍 ≥10（避小基数门）
    ]
    signals = detect_signals_from_snapshots(snapshots)
    by_id = {s.skill_id: s for s in signals}
    # historical=[40,0,40,40] → mean=30, std=20 → z=(12-30)/20=-0.9：真实断档拉宽 σ，
    # 轻微回落不再被断续子序列放大成伪 declining
    assert by_id["sk_g"].historical_mean == 30.0
    assert by_id["sk_g"].historical_std == 20.0
    assert by_id["sk_g"].z_score == -0.9
    assert by_id["sk_g"].trend == SkillEvolutionTrend.STABLE
