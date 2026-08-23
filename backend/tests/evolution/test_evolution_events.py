"""谱系事件分类（机制补强② born/merged/ended，PR #334 确认）纯函数测试。

覆盖：mixed 三分类（merged/born/ended）、suppress_ended（与①样本量告警联动）、
单配对（rename/split）不产生事件。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.evolution.evolved_from import classify_evolution_events


def _types(events):
    return [e["event_type"] for e in events]


def test_mixed_classification():
    new_names = {"平台融合工程师", "AI平台工程师"}
    gone_names = {"后端工程师", "前端工程师", "老岗位C"}
    matched = {"平台融合工程师": ["后端工程师", "前端工程师"]}  # 一 new 含两 old → merged

    events = classify_evolution_events(new_names, gone_names, matched, suppress_ended=False)

    ts = _types(events)
    assert "merged" in ts
    assert "born" in ts       # AI平台工程师 未配对 → 涌现
    assert "ended" in ts      # 老岗位C 未配对 → 消亡
    merged = next(e for e in events if e["event_type"] == "merged")
    assert merged["detail"]["from_names"] == ["前端工程师", "后端工程师"]  # sorted(old)
    assert merged["to_name"] == "平台融合工程师"


def test_suppress_ended_with_data_warning():
    # D4：当前版本命中①样本量告警 → 抑制 ended（防采集停摆误报岗位消亡）
    events = classify_evolution_events({"新A"}, {"旧B"}, {}, suppress_ended=True)
    assert "ended" not in _types(events)
    assert "born" in _types(events)


def test_single_pairing_produces_no_event():
    # 单一 rename/split 配对 → new/gone 均已配对，born/merged/ended 均不触发
    events = classify_evolution_events({"新A"}, {"旧B"}, {"新A": ["旧B"]}, suppress_ended=False)
    assert events == []


def test_empty_sets_no_events():
    assert classify_evolution_events(set(), set(), {}, suppress_ended=False) == []
