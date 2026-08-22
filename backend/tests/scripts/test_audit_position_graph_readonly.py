"""Unit tests for read-only position/graph reconciliation."""

from scripts.audit_position_graph_readonly import (
    _GRAPH_CAREER_QUERY,
    _GRAPH_POSITIONS_QUERY,
    _GRAPH_REQUIRES_QUERY,
    reconcile,
)


def test_reconcile_reports_required_coverage_and_drift_dimensions():
    report = reconcile(
        snapshots=[
            {"extraction": {"position_name": "前端开发"}},
            {"extraction": {"position_name": "算法工程师", "skills": [{"name": "计算机视觉"}]}},
            {"extraction": {}},
            {},
        ],
        graph_positions={"前端开发工程师", "数据分析师"},
        requires=[
            {"position": "前端开发工程师", "skill": "React", "level": "熟悉"},
            {"position": "数据分析师", "skill": "SQL", "level": ""},
            {"position": "数据分析师", "skill": "Python", "level": "大师"},
        ],
        career_rows=[
            {"position": "前端开发工程师", "occupation_relations": 1},
            {"position": "数据分析师", "occupation_relations": 0},
        ],
    )

    assert report["read_only"] is True
    assert report["normalization"] == {
        "pg_jd_rows": 4,
        "normalized_rows": 2,
        "normalized_position_count": 2,
        "empty_reason_counts": {"缺少 extraction": 1, "缺少 extraction.position_name": 1},
    }
    assert report["position_set_difference"] == {
        "pg_only_count": 1,
        "neo4j_only_count": 1,
        "pg_only": ["机器视觉算法工程师"],
        "neo4j_only": ["数据分析师"],
    }
    assert report["requires_level"] == {
        "edge_count": 3,
        "filled_count": 2,
        "missing_count": 1,
        "legal_level_counts": {"初级": 1},
        "invalid_count": 1,
        "invalid": [{"position": "数据分析师", "skill": "Python", "level": "大师"}],
    }
    assert report["occupation_relation"] == {
        "position_count": 2,
        "covered_count": 1,
        "uncovered_count": 1,
        "uncovered_positions": ["数据分析师"],
    }


def test_graph_queries_are_read_only_match_return_queries():
    forbidden = (" CREATE ", " MERGE ", " SET ", " DELETE ", " REMOVE ", " DROP ")
    for query in (_GRAPH_POSITIONS_QUERY, _GRAPH_REQUIRES_QUERY, _GRAPH_CAREER_QUERY):
        normalized = f" {query.upper()} "
        assert "MATCH" in normalized
        assert "RETURN" in normalized
        assert not any(token in normalized for token in forbidden)
