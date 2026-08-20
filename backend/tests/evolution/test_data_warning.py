"""样本量对比告警（机制补强 ①，PR #334 确认 50%/200%）纯函数测试。

覆盖：首个快照无告警、未越界无告警、萎缩<50% 告警、膨胀>200% 告警、
仅越界维度告警、恰好落在边界（0.5/2.0）不告警。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.evolution.graph_version import compute_data_warning


def _nodes(position_count: int) -> list[dict]:
    return [{"id": f"pos-{i}", "name": f"岗位{i}", "type": "position"} for i in range(position_count)]


def _edges(requires: int) -> list[dict]:
    return (
        [{"source": f"s{i}", "target": f"t{i}", "relation": "REQUIRES"} for i in range(requires)]
        + [{"source": f"x{i}", "target": f"y{i}", "relation": "OTHER"} for i in range(2)]
    )


def test_first_snapshot_no_warning():
    # 无上一版本（prev 全空）→ 无法对比，不告警
    assert compute_data_warning([], _nodes(10), [], _edges(30)) is None


def test_no_crossing_no_warning():
    # 1.0x / 1.033x，未越界
    assert compute_data_warning(_nodes(100), _nodes(100), _edges(300), _edges(310)) is None


def test_shrunk_below_half_warns():
    w = compute_data_warning(_nodes(100), _nodes(48), _edges(300), _edges(149))
    assert w is not None
    assert w["positions"]["direction"] == "shrunk"
    assert w["requires_edges"]["direction"] == "shrunk"
    assert w["positions"]["ratio"] < 0.5


def test_surged_above_double_warns():
    w = compute_data_warning(_nodes(50), _nodes(110), _edges(100), _edges(210))
    assert w is not None
    assert w["positions"]["direction"] == "surged"


def test_only_crossed_metric_warns():
    # 岗位数 1.0x 未越界；REQUIRES 0.4x 越界 → 仅告警 requires_edges
    w = compute_data_warning(_nodes(100), _nodes(100), _edges(100), _edges(40))
    assert w is not None and "requires_edges" in w and "positions" not in w


def test_exact_boundary_not_warn():
    # 恰在 0.5 / 2.0（严格 < / > 判定）→ 不告警
    assert compute_data_warning(_nodes(10), _nodes(5), _edges(10), _edges(5)) is None
    assert compute_data_warning(_nodes(10), _nodes(20), _edges(10), _edges(20)) is None
