"""岗位状态对账测试（08-16 审查：风险 B 写序漂移检测 + 风险 C 口径区分）。

覆盖 _reconcile 的三类结果（一致/漂移/图谱缺失）与 candidate 预期未入图口径，
以及 _fix_drift 的 MATCH 回写行为（不建孤儿节点）。
"""

from scripts.audit_position_status import _fix_drift, _reconcile


class _FakeNeo4j:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def session(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query: str, **params):
        self.calls.append((query, params))


def test_consistent_states_no_drift():
    """图谱状态与候选池一致时无漂移无缺失。"""
    candidates = {"算法工程师": "emerging", "鸿蒙开发工程师": "archived"}
    graph = {"算法工程师": "emerging", "鸿蒙开发工程师": "archived"}
    result = _reconcile(candidates, graph)
    assert result["drift"] == []
    assert result["missing"] == []
    assert result["expected_missing_candidates"] == 0


def test_drift_detected():
    """状态漂移（PG=emerging 图谱=active，写序失败/重建场景）被检出。"""
    candidates = {"算法工程师": "emerging", "稳定岗位": "stable"}
    graph = {"算法工程师": "active", "稳定岗位": "stable"}
    result = _reconcile(candidates, graph)
    assert len(result["drift"]) == 1
    assert result["drift"][0] == {
        "position_name": "算法工程师",
        "pg_state": "emerging",
        "graph_status": "active",
    }


def test_candidate_missing_is_expected():
    """candidate 未入图属预期（风险 C 口径），不告警不进 missing。"""
    candidates = {"趋势岗位": "candidate"}
    result = _reconcile(candidates, {})
    assert result["drift"] == []
    assert result["missing"] == []
    assert result["expected_missing_candidates"] == 1


def test_reviewed_missing_from_graph():
    """已审核岗位图谱无节点（重建后无 JD 支撑）→ missing 报告。"""
    candidates = {"已归档岗位": "archived"}
    result = _reconcile(candidates, {})
    assert result["missing"] == [{"position_name": "已归档岗位", "pg_state": "archived"}]
    assert result["drift"] == []


def test_fix_drift_writes_matched_status(monkeypatch):
    """--fix 按 PG 回写漂移项：MATCH（不建孤儿）+ state_updated_at。"""
    drift = [
        {"position_name": "算法工程师", "pg_state": "emerging", "graph_status": "active"},
        {"position_name": "旧岗位", "pg_state": "archived", "graph_status": "declining"},
    ]
    neo4j = _FakeNeo4j()
    monkeypatch.setattr("scripts.audit_position_status.neo4j_driver", neo4j)

    fixed = _fix_drift(drift)

    assert fixed == 2
    assert len(neo4j.calls) == 2
    for query, params in neo4j.calls:
        assert "MATCH (p:Position {name: $name})" in query
        assert "SET p.status = $state" in query
        assert "state_updated_at" in query
    assert {c[1]["state"] for c in neo4j.calls} == {"emerging", "archived"}


def test_fix_drift_empty_no_write(monkeypatch):
    """无漂移时不触发 Neo4j 写入。"""
    neo4j = _FakeNeo4j()
    monkeypatch.setattr("scripts.audit_position_status.neo4j_driver", neo4j)
    assert _fix_drift([]) == 0
    assert neo4j.calls == []
