"""图谱版本 Diff 单元测试（设计文档 7.1 版本管理）。"""

from app.api.v1.evolution import _diff_snapshots


def _snapshot(nodes: list[str], edges: list[tuple[str, str]]) -> dict:
    return {
        "nodes": [{"id": n} for n in nodes],
        "edges": [{"source": s, "target": t} for s, t in edges],
    }


class TestDiffSnapshots:
    def test_identical_snapshots_no_diff(self):
        a = _snapshot(["p1", "s1"], [("p1", "s1")])
        diff = _diff_snapshots(a, a)
        assert diff == {
            "nodes_added": [], "nodes_removed": [], "nodes_changed": ["p1", "s1"],
            "edges_added": [], "edges_removed": [],
        }

    def test_detects_added_and_removed_nodes(self):
        a = _snapshot(["p1", "s1"], [("p1", "s1")])
        b = _snapshot(["p1", "s1", "s2"], [("p1", "s1"), ("p1", "s2")])
        diff = _diff_snapshots(a, b)
        assert diff["nodes_added"] == ["s2"]
        assert diff["nodes_removed"] == []
        assert diff["edges_added"] == ["p1->s2"]
        assert diff["edges_removed"] == []

    def test_detects_removed_nodes_and_edges(self):
        a = _snapshot(["p1", "s1", "s2"], [("p1", "s1"), ("p1", "s2")])
        b = _snapshot(["p1", "s1"], [("p1", "s1")])
        diff = _diff_snapshots(a, b)
        assert diff["nodes_removed"] == ["s2"]
        assert diff["edges_removed"] == ["p1->s2"]

    def test_empty_snapshots(self):
        diff = _diff_snapshots({}, {})
        assert diff["nodes_added"] == []
        assert diff["nodes_changed"] == []
