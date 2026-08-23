"""抗波动补强（打标不剔除）测试：

1. _annotate_anti_fluctuation：解读期告警打标 + freq_ratio 归一化口径
2. watch_pool 平台健康守卫：采集故障周识别与剔除
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.api.v1.evolution import _annotate_anti_fluctuation
from app.services.discovery.watch_pool import (
    drop_unhealthy_weeks,
    platform_weekly_totals,
    unhealthy_week_keys,
)
from app.services.evolution.schemas import EvolutionSignal, SkillEvolutionTrend


def _signal(freq: int = 20) -> EvolutionSignal:
    return EvolutionSignal(
        skill_id="sk_x",
        skill_name="技能X",
        current_freq=freq,
        trend=SkillEvolutionTrend.DECLINING,
        confidence=0.6,
    )


def _snapshot(requires: int) -> dict:
    return {
        "nodes": [],
        "edges": [
            {"source": f"p{i}", "target": f"sk_{i}", "relation": "REQUIRES"}
            for i in range(requires)
        ],
    }


# ---- _annotate_anti_fluctuation ----

def test_no_warning_when_interpretation_window_clean():
    rows = [SimpleNamespace(data_warning=None)] * 3
    s = _signal()
    _annotate_anti_fluctuation(rows, [_snapshot(10)] * 3, [s])
    assert s.warning is False
    assert s.freq_ratio == 2.0  # 20 / 10


def test_warning_flagged_when_latest_two_hit_data_warning():
    # 解读期 = 最近两期，任一命中即打标
    rows = [
        SimpleNamespace(data_warning=None),
        SimpleNamespace(data_warning={"positions": {}}),
        SimpleNamespace(data_warning=None),
    ]
    s = _signal()
    _annotate_anti_fluctuation(rows, [_snapshot(10)] * 3, [s])
    assert s.warning is True


def test_old_warning_not_flagged():
    # 告警在更早版本（不在最近两期）→ 不打标
    rows = [
        SimpleNamespace(data_warning={"positions": {}}),
        SimpleNamespace(data_warning=None),
        SimpleNamespace(data_warning=None),
    ]
    s = _signal()
    _annotate_anti_fluctuation(rows, [_snapshot(10)] * 3, [s])
    assert s.warning is False


def test_freq_ratio_none_when_zero_denominator():
    s = _signal()
    _annotate_anti_fluctuation([SimpleNamespace(data_warning=None)], [_snapshot(0)], [s])
    assert s.freq_ratio is None


def test_freq_ratio_counts_only_requires_edges():
    snap = _snapshot(8)
    snap["edges"].extend(
        {"source": f"a{i}", "target": f"b{i}", "relation": "TAUGHT_BY"} for i in range(10)
    )
    s = _signal(freq=4)
    _annotate_anti_fluctuation([SimpleNamespace(data_warning=None)], [snap], [s])
    assert s.freq_ratio == 0.5


# ---- 平台健康守卫 ----

def _freqs(source: str, weeks: dict[str, int]) -> dict:
    # 单技能单源：skill 名与周总量一致，便于直接控制平台周总量
    return {("LangChain", source): dict(weeks)}


def test_platform_weekly_totals_sums_skills():
    freqs = {
        ("A", "boss"): {"2026-W01": 3},
        ("B", "boss"): {"2026-W01": 4},
        ("A", "arxiv"): {"2026-W01": 5},
    }
    totals = platform_weekly_totals(freqs)
    assert totals["boss"]["2026-W01"] == 7
    assert totals["arxiv"]["2026-W01"] == 5


def test_outage_week_detected_and_dropped():
    # boss 健康周 40/42/38，故障周 5，最新周 45（不评估）
    weeks = {
        "2026-W28": 40, "2026-W29": 42, "2026-W30": 38,
        "2026-W31": 5, "2026-W32": 45,
    }
    freqs = _freqs("boss", weeks)
    unhealthy = unhealthy_week_keys(freqs)
    assert ("boss", "2026-W31") in unhealthy
    # 剔除后故障周样本消失
    dropped = drop_unhealthy_weeks(freqs, unhealthy)
    assert "2026-W31" not in dropped[("LangChain", "boss")]


def test_latest_week_never_evaluated():
    # 最新周总量极低（任务运行当周未满）也不应判为故障周
    weeks = {"2026-W28": 40, "2026-W29": 42, "2026-W30": 38, "2026-W31": 2}
    assert ("boss", "2026-W31") not in unhealthy_week_keys(_freqs("boss", weeks))


def test_short_history_not_evaluated():
    # 历史不足 3 周 → 不评估
    weeks = {"2026-W30": 100, "2026-W31": 1}
    assert unhealthy_week_keys(_freqs("boss", weeks)) == set()


def test_normal_fluctuation_kept():
    # ±20% 波动属正常采集噪声，不剔除
    weeks = {"2026-W28": 100, "2026-W29": 80, "2026-W30": 120, "2026-W31": 90}
    assert unhealthy_week_keys(_freqs("boss", weeks)) == set()


def test_drop_removes_empty_entries():
    freqs = {
        ("A", "boss"): {"2026-W31": 3},          # boss 唯一周为故障周 → 整条移除
        ("B", "arxiv"): {"2026-W31": 3},          # arxiv 无故障 → 保留
    }
    dropped = drop_unhealthy_weeks(freqs, {("boss", "2026-W31")})
    assert ("A", "boss") not in dropped
    assert ("B", "arxiv") in dropped
