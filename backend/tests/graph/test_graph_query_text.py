"""graph 异步查询函数文本级断言（08-14 审查：integration 层默认 skip 形同虚设，
真实行为验证补查询文本级回归网——不依赖 Neo4j 基础设施）。

08-17 P2 迁移后热路径（panorama / search / view 等）改走 async 驱动
（async_neo4j_driver，见 database.py），本文件 fake 同步改 async：驱动
session 上下文、session.run、结果迭代/single/data 全异步化，断言内容
（Cypher 状态过滤子句/参数）与迁移前逐项一致。

覆盖：_query_panorama / _query_view_techstack / _query_view_main /
_query_fulltext_search / _query_skill_positions 的 Cypher 是否带状态过滤
（匿名/guest 不得外宣 candidate 岗位，方案一）。
"""

import pytest

from app.api.v1 import graph as graph_api


class _FakeResult:
    """空结果：async 可迭代 + data()/single()（count/单行查询）。"""

    def __init__(self, rows=()):
        self._rows = rows

    def __aiter__(self):
        async def _gen():
            for row in self._rows:
                yield row

        return _gen()

    async def data(self):
        return list(self._rows)

    async def fetch(self, number):
        return list(self._rows[:number])

    async def single(self):
        return None


class _FakeSession:
    """捕获 run 查询的 fake Neo4j async session（返回空异步结果）。"""

    def __init__(self):
        self.queries: list[tuple[str, dict]] = []

    async def run(self, query, **params):
        self.queries.append((query, params))
        return _FakeResult()


class _FakeSessionCtx:
    """async with driver.session() as session 的上下文管理器。"""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


class _FakeDriver:
    def __init__(self):
        self.sessions: list[_FakeSession] = []

    def session(self):
        s = _FakeSession()
        self.sessions.append(s)
        return _FakeSessionCtx(s)


def _install(monkeypatch) -> _FakeDriver:
    driver = _FakeDriver()
    monkeypatch.setattr(graph_api, "async_neo4j_driver", driver)
    return driver


class TestPanoramaStatusFilter:
    @pytest.mark.asyncio
    async def test_public_scope_has_status_filter(self, monkeypatch):
        driver = _install(monkeypatch)
        await graph_api._query_panorama("public", None, 0.3, 100)
        query, params = driver.sessions[0].queries[0]
        assert "p.status IN $public_statuses" in query
        assert params["public_statuses"] == list(graph_api._PUBLIC_POSITION_STATUSES)

    @pytest.mark.asyncio
    async def test_all_scope_no_status_filter(self, monkeypatch):
        driver = _install(monkeypatch)
        await graph_api._query_panorama("all", None, 0.3, 100)
        query, _ = driver.sessions[0].queries[0]
        assert "p.status IN $public_statuses" not in query

    @pytest.mark.asyncio
    async def test_focus_branch_has_status_filter(self, monkeypatch):
        driver = _install(monkeypatch)
        await graph_api._query_panorama("public", "pos_0001", 0.3, 100)
        query, params = driver.sessions[0].queries[0]
        assert "p.status IN $public_statuses" in query
        assert params["focus"] == "pos_0001"
        assert params["min_weight"] == 0.3


class TestViewStatusFilter:
    @pytest.mark.asyncio
    async def test_techstack_public_statuses_passed(self, monkeypatch):
        driver = _install(monkeypatch)
        rows = await graph_api._query_view_techstack(
            50, "p.status IN $public_statuses"
        )
        assert rows == []
        query, params = driver.sessions[0].queries[0]
        assert "WHERE p.status IN $public_statuses" in query
        assert params["limit"] == 50
        assert params["public_statuses"] == list(graph_api._PUBLIC_POSITION_STATUSES)

    @pytest.mark.asyncio
    async def test_main_view_public_statuses_passed(self, monkeypatch):
        driver = _install(monkeypatch)
        await graph_api._query_view_main(50, "p.status IN $public_statuses")
        query, params = driver.sessions[0].queries[0]
        assert "WHERE p.status IN $public_statuses" in query
        assert params["public_statuses"] == list(graph_api._PUBLIC_POSITION_STATUSES)


class TestSkillCategoryExposure:
    """techStack 视图 Cypher 须带回技能类目（软技能/技术栈区分展示的数据来源）。"""

    @pytest.mark.asyncio
    async def test_techstack_query_returns_category(self, monkeypatch):
        driver = _install(monkeypatch)
        await graph_api._query_view_techstack(50, "p.status IN $public_statuses")
        query, _ = driver.sessions[0].queries[0]
        assert "s.category AS s_category" in query


class TestFulltextStatusFilter:
    @pytest.mark.asyncio
    async def test_position_public_scope_filters_status(self, monkeypatch):
        driver = _install(monkeypatch)
        await graph_api._query_fulltext_search(
            "Python", "position", "WHERE node.status IN $public_statuses", 0, 20
        )
        query, params = driver.sessions[0].queries[0]
        assert "node.status IN $public_statuses" in query
        assert params["public_statuses"] == list(graph_api._PUBLIC_POSITION_STATUSES)

    @pytest.mark.asyncio
    async def test_skill_scope_no_status_filter(self, monkeypatch):
        driver = _install(monkeypatch)
        await graph_api._query_fulltext_search("Python", "skill", "", 0, 20)
        query, _ = driver.sessions[0].queries[0]
        assert "node.status" not in query


class TestSkillPositionsStatusFilter:
    @pytest.mark.asyncio
    async def test_public_status_filter_passed(self, monkeypatch):
        driver = _install(monkeypatch)
        await graph_api._query_skill_positions(
            "sk_0001", "p.status IN $public_statuses"
        )
        query, params = driver.sessions[0].queries[0]
        assert "p.status IN $public_statuses" in query
        assert params["public_statuses"] == list(graph_api._PUBLIC_POSITION_STATUSES)
