"""Z-score 占比口径归一化（PR-2，挂张恺天确认）测试。

核心场景：采集总量骤降（爬虫故障）→ 全技能频次齐跌 → 占比不变 →
不再批量误判 declining；真实占比上升仍能检出 emerging。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.evolution.detector import EvolutionDetector
from app.services.evolution.schemas import (
    SkillEvolutionTrend,
    SkillFrequencyWindow,
)
from app.services.evolution.trend_service import detect_signals_from_snapshots


def _win(freq: int, total: int = 0) -> SkillFrequencyWindow:
    return SkillFrequencyWindow(
        skill_id="sk_x",
        skill_name="X",
        window_start="",
        window_end="",
        frequency=freq,
        total_requires=total,
    )


def test_total_volume_halved_ratio_constant_not_declining():
    # 历史：频次 60/1000，当前采集量腰斩：30/500 → 占比同为 0.06 → stable
    detector = EvolutionDetector()
    signal = detector.detect_skill("sk_x", _win(30, 500), [_win(60, 1000), _win(62, 1000)])
    assert signal.trend == SkillEvolutionTrend.STABLE
    assert abs(signal.z_score) < 1.5


def test_raw_count_declining_scenario_neutralized():
    # 原始计数口径下 60→30 会被判 declining（z = -1.9）；占比口径下不判
    # （历史占比 0.06/0.06，当前 0.06，std=0 → z=0）
    detector = EvolutionDetector()
    signal = detector.detect_skill("sk_x", _win(30, 500), [_win(60, 1000), _win(60, 1000)])
    assert signal.trend == SkillEvolutionTrend.STABLE
    assert signal.z_score == 0.0


def test_genuine_ratio_rise_still_emerging():
    # 总量平稳但技能占比上升：20/1000 → 90/1000 → emerging 检出不受归一化影响
    detector = EvolutionDetector()
    signal = detector.detect_skill(
        "sk_x", _win(90, 1000), [_win(20, 1000), _win(22, 1000)]
    )
    assert signal.trend == SkillEvolutionTrend.EMERGING


def test_zero_total_falls_back_to_raw_counts():
    # total_requires=0（旧数据无 relation 字段）→ 退回原始计数，行为与归一化前一致
    detector = EvolutionDetector()
    signal = detector.detect_skill("sk_x", _win(90), [_win(20), _win(22)])
    assert signal.trend == SkillEvolutionTrend.EMERGING


def test_snapshots_without_relation_keep_legacy_behavior():
    # 快照 edges 无 relation 字段（历史测试夹具形态）→ 分母 0 → 兼容旧口径
    def snap(version: str, freq: int) -> dict:
        return {
            "nodes": [{"id": "sk_x", "name": "X", "type": "skill"}],
            "edges": [{"source": f"pos_{i}", "target": "sk_x"} for i in range(freq)],
            "version_id": version,
        }

    signals = detect_signals_from_snapshots([snap("v1", 20), snap("v2", 22), snap("v3", 90)])
    by_id = {s.skill_id: s for s in signals}
    assert by_id["sk_x"].trend == SkillEvolutionTrend.EMERGING


def test_snapshots_with_relation_use_ratio():
    # 带 relation 的快照走占比口径：总量腰斩频次腰斩 → 不判 declining
    def snap(version: str, freq: int, other: int) -> dict:
        edges = [{"source": f"pos_{i}", "target": "sk_x", "relation": "REQUIRES"} for i in range(freq)]
        edges += [
            {"source": f"q_{i}", "target": f"sk_o{i}", "relation": "REQUIRES"} for i in range(other)
        ]
        return {
            "nodes": [{"id": "sk_x", "name": "X", "type": "skill"}],
            "edges": edges,
            "version_id": version,
        }

    # 历史占比 60/1060；当前总量减半、频次减半 → 占比 30/530 ≈ 历史 → stable
    signals = detect_signals_from_snapshots(
        [snap("v1", 60, 1000), snap("v2", 62, 1000), snap("v3", 30, 500)]
    )
    by_id = {s.skill_id: s for s in signals}
    assert by_id["sk_x"].trend != SkillEvolutionTrend.DECLINING


# ── 评审三确认项修复回归（负责人拍板 A-1①/A-2①/A-3①，08-22）──


def test_mixed_caliber_series_falls_back_to_counts_whole_series():
    """A-2① 回归：新旧口径混排序列整列退回计数口径。

    历史为计数口径（无分母），当前窗口有占比分母——逐窗口独立 fallback 时
    current 取占比 0.09 而历史均值 ~61，z ≈ −60 批量伪 declining；整列同
    口径后全序列用计数，真实上升仍判 emerging。
    """
    detector = EvolutionDetector()
    signal = detector.detect_skill("sk_x", _win(90, 1000), [_win(60), _win(62), _win(61)])
    assert signal.trend == SkillEvolutionTrend.EMERGING


def test_numerator_counts_requires_edges_only():
    """A-1① 回归：分子仅计 REQUIRES 边，BELONGS_TO 等技能→技能边不混入。

    分母本就只数 REQUIRES；分子若混入 BELONGS_TO 则占比可 >1，且与
    state_machine 的 REQUIRES 过滤约定相悖。
    """
    from app.services.evolution.trend_service import _skill_freq_windows

    snap = {
        "nodes": [{"id": "sk_x", "name": "X", "type": "skill"}],
        "edges": (
            [{"source": f"pos_{i}", "target": "sk_x", "relation": "REQUIRES"} for i in range(3)]
            + [{"source": f"sk_p{i}", "target": "sk_x", "relation": "BELONGS_TO"} for i in range(5)]
        ),
        "version_id": "v1",
    }
    seq = _skill_freq_windows([snap])["sk_x"]
    assert seq[0].frequency == 3
    assert seq[0].total_requires == 3


def _ratio_snap(version: str, freq: int, other: int) -> dict:
    edges = [{"source": f"pos_{i}", "target": "sk_x", "relation": "REQUIRES"} for i in range(freq)]
    edges += [
        {"source": f"q_{i}", "target": f"sk_o{i}", "relation": "REQUIRES"} for i in range(other)
    ]
    return {
        "nodes": [{"id": "sk_x", "name": "X", "type": "skill"}],
        "edges": edges,
        "version_id": version,
    }


def test_degraded_snapshot_excluded_no_reverse_pseudo_emerging():
    """A-3① 回归：命中 data_warning 的快照整期剔除。

    第三期部分源故障（REQUIRES 总量 1060→160，compute_data_warning 必然
    告警）：技能频次未降而占比翻数倍，不剔除时伪 emerging；剔除后该期
    既不作 current 也不进 μ/σ。
    """
    snaps = [
        _ratio_snap("v1", 58, 1000),
        _ratio_snap("v2", 62, 1000),
        _ratio_snap("v3", 60, 100),  # 总量骤降、sk_x 计数不变 → 占比虚高
    ]

    unsuppressed = detect_signals_from_snapshots(snaps)
    by_id = {s.skill_id: s for s in unsuppressed}
    assert by_id["sk_x"].trend == SkillEvolutionTrend.EMERGING  # 前提锚点：确为伪信号

    suppressed = detect_signals_from_snapshots(snaps, degraded_flags=[False, False, True])
    by_id = {s.skill_id: s for s in suppressed}
    assert by_id["sk_x"].trend == SkillEvolutionTrend.STABLE  # 只剩两期同况快照 → z=0


def test_all_snapshots_degraded_returns_empty():
    """A-3① 边界：全部快照被剔除 → 数据不足返回空，不武断判定。"""
    assert detect_signals_from_snapshots(
        [_ratio_snap("v1", 60, 1000), _ratio_snap("v2", 62, 1000)],
        degraded_flags=[True, True],
    ) == []
