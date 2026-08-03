"""图谱版本 Diff 单元测试（设计文档 7.1 版本管理）。"""

from app.api.v1.evolution import _diff_snapshots


def _snapshot(nodes: list[dict], edges: list[tuple[str, str]]) -> dict:
    return {
        "nodes": nodes,
        "edges": [{"source": s, "target": t} for s, t in edges],
    }


def _node(nid: str, name: str = "", type_: str = "") -> dict:
    return {"id": nid, "name": name or nid, "type": type_}


class TestDiffSnapshots:
    def test_identical_snapshots_no_diff(self):
        a = _snapshot([_node("p1", "后端工程师", "position"), _node("s1", "Python", "skill")], [("p1", "s1")])
        diff = _diff_snapshots(a, a)
        assert diff == {
            "nodes_added": [],
            "nodes_removed": [],
            # 交集中的节点带真实名称返回
            "nodes_changed": [
                {"id": "p1", "name": "后端工程师", "type": "position"},
                {"id": "s1", "name": "Python", "type": "skill"},
            ],
            "edges_added": [],
            "edges_removed": [],
        }

    def test_detects_added_and_removed_nodes(self):
        a = _snapshot([_node("p1", "后端工程师"), _node("s1", "Python")], [("p1", "s1")])
        b = _snapshot(
            [_node("p1", "后端工程师"), _node("s1", "Python"), _node("s2", "React", "skill")],
            [("p1", "s1"), ("p1", "s2")],
        )
        diff = _diff_snapshots(a, b)
        assert diff["nodes_added"] == [{"id": "s2", "name": "React", "type": "skill"}]
        assert diff["nodes_removed"] == []
        assert diff["edges_added"] == ["p1->s2"]
        assert diff["edges_removed"] == []

    def test_detects_removed_nodes_and_edges(self):
        a = _snapshot(
            [_node("p1", "后端工程师"), _node("s1", "Python"), _node("s2", "Vue", "skill")],
            [("p1", "s1"), ("p1", "s2")],
        )
        b = _snapshot([_node("p1", "后端工程师"), _node("s1", "Python")], [("p1", "s1")])
        diff = _diff_snapshots(a, b)
        # 删除节点的名称回退旧版本快照
        assert diff["nodes_removed"] == [{"id": "s2", "name": "Vue", "type": "skill"}]
        assert diff["edges_removed"] == ["p1->s2"]

    def test_empty_snapshots(self):
        diff = _diff_snapshots({}, {})
        assert diff["nodes_added"] == []
        assert diff["nodes_changed"] == []

    def test_node_without_name_falls_back_to_id(self):
        a = _snapshot([_node("ev_1", "")], [])
        b = _snapshot([_node("ev_1", ""), _node("ev_2", "")], [])
        diff = _diff_snapshots(a, b)
        # 无 name 属性时退回 id，避免前端展示空串
        assert diff["nodes_added"] == [{"id": "ev_2", "name": "ev_2", "type": ""}]
        assert diff["nodes_changed"] == [{"id": "ev_1", "name": "ev_1", "type": ""}]
