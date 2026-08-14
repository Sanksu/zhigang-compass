"""graph 同步查询函数文本级断言（08-14 审查：integration 层默认 skip 形同虚设，
真实行为验证补查询文本级回归网——不依赖 Neo4j 基础设施）。

覆盖：_query_panorama / _query_view_techstack / _query_view_main /
_query_fulltext_search / _query_skill_positions 的 Cypher 是否带状态过滤
（匿名/guest 不得外宣 candidate 岗位，方案一）。
"""

from app.api.v1 import graph as graph_api


class _FakeResult:
    """空结果：可迭代（records）+ single() 返回 None（count 查询）。"""

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def single(self):
        return None


class _FakeSession:
    """捕获 run 查询的 fake Neo4j session（返回空结果）。"""

    def __init__(self):
        self.queries: list[tuple[str, dict]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query, **params):
        self.queries.append((query, params))
        return _FakeResult()


class _FakeDriver:
    def __init__(self):
        self.sessions: list[_FakeSession] = []

    def session(self):
        s = _FakeSession()
        self.sessions.append(s)
        return s


def _install(monkeypatch) -> _FakeDriver:
    driver = _FakeDriver()
    monkeypatch.setattr(graph_api, "neo4j_driver", driver)
    return driver


class TestPanoramaStatusFilter:
    def test_public_scope_has_status_filter(self, monkeypatch):
        driver = _install(monkeypatch)
        graph_api._query_panorama("public", None, 0.3, 100)
        query, params = driver.sessions[0].queries[0]
        assert "p.status IN $public_statuses" in query
        assert params["public_statuses"] == list(graph_api._PUBLIC_POSITION_STATUSES)

    def test_all_scope_no_status_filter(self, monkeypatch):
        driver = _install(monkeypatch)
        graph_api._query_panorama("all", None, 0.3, 100)
        query, _ = driver.sessions[0].queries[0]
        assert "p.status IN $public_statuses" not in query

    def test_focus_branch_has_status_filter(self, monkeypatch):
        driver = _install(monkeypatch)
        graph_api._query_panorama("public", "pos_0001", 0.3, 100)
        query, params = driver.sessions[0].queries[0]
        assert "p.status IN $public_statuses" in query
        assert params["focus"] == "pos_0001"
        assert params["min_weight"] == 0.3


class TestViewStatusFilter:
    def test_techstack_public_statuses_passed(self, monkeypatch):
        driver = _install(monkeypatch)
        rows = graph_api._query_view_techstack(
            50, "p.status IN $public_statuses"
        )
        assert rows == []
        query, params = driver.sessions[0].queries[0]
        assert "WHERE p.status IN $public_statuses" in query
        assert params["limit"] == 50
        assert params["public_statuses"] == list(graph_api._PUBLIC_POSITION_STATUSES)

    def test_main_view_public_statuses_passed(self, monkeypatch):
        driver = _install(monkeypatch)
        graph_api._query_view_main(50, "p.status IN $public_statuses")
        query, params = driver.sessions[0].queries[0]
        assert "WHERE p.status IN $public_statuses" in query
        assert params["public_statuses"] == list(graph_api._PUBLIC_POSITION_STATUSES)


class TestFulltextStatusFilter:
    def test_position_public_scope_filters_status(self, monkeypatch):
        driver = _install(monkeypatch)
        graph_api._query_fulltext_search(
            "Python", "position", "WHERE node.status IN $public_statuses", 0, 20
        )
        query, params = driver.sessions[0].queries[0]
        assert "node.status IN $public_statuses" in query
        assert params["public_statuses"] == list(graph_api._PUBLIC_POSITION_STATUSES)

    def test_skill_scope_no_status_filter(self, monkeypatch):
        driver = _install(monkeypatch)
        graph_api._query_fulltext_search("Python", "skill", "", 0, 20)
        query, _ = driver.sessions[0].queries[0]
        assert "node.status" not in query


class TestSkillPositionsStatusFilter:
    def test_public_status_filter_passed(self, monkeypatch):
        driver = _install(monkeypatch)
        graph_api._query_skill_positions(
            "sk_0001", "p.status IN $public_statuses"
        )
        query, params = driver.sessions[0].queries[0]
        assert "p.status IN $public_statuses" in query
        assert params["public_statuses"] == list(graph_api._PUBLIC_POSITION_STATUSES)
