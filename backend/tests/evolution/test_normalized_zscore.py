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
