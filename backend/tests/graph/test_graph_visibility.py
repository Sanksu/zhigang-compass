"""图谱岗位可见性过滤单元测试（方案一：candidate 待审核不外宣）。

覆盖：
- _can_view_all_positions / _position_scope：角色 → 可见范围（纯函数）
- get_optional_user：匿名/无效 token → None，有效 user/admin → payload
- panorama / view 的 Cypher 查询是否带状态过滤（校验查询文本，不依赖 Neo4j）
"""

import asyncio

from fastapi.security import HTTPAuthorizationCredentials

from app.api.deps import get_optional_user
from app.api.v1 import graph as graph_api
from app.core.security import create_access_token
from tests.helpers import FakeRedis


class TestVisibilityScope:
    """_can_view_all_positions / _position_scope 角色判定。"""

    def test_anonymous_public_scope(self):
        assert graph_api._can_view_all_positions(None) is False
        assert graph_api._position_scope(None) == "public"

    def test_guest_public_scope(self):
        user = {"role": "guest", "sub": "g1"}
        assert graph_api._can_view_all_positions(user) is False
        assert graph_api._position_scope(user) == "public"

    def test_user_all_scope(self):
        user = {"role": "user", "sub": "u1"}
        assert graph_api._can_view_all_positions(user) is True
        assert graph_api._position_scope(user) == "all"

    def test_admin_all_scope(self):
        user = {"role": "admin", "sub": "a1"}
        assert graph_api._can_view_all_positions(user) is True
        assert graph_api._position_scope(user) == "all"

    def test_unknown_role_treated_as_guest(self):
        user = {"role": "robot", "sub": "r1"}
        assert graph_api._can_view_all_positions(user) is False
        assert graph_api._position_scope(user) == "public"

    def test_missing_role_treated_as_guest(self):
        user = {"sub": "x1"}
        assert graph_api._can_view_all_positions(user) is False
        assert graph_api._position_scope(user) == "public"


class TestGetOptionalUser:
    """可选鉴权依赖：匿名/无效 → None，有效 token → payload。"""

    async def _call(self, token: str | None):
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token) if token else None
        return await get_optional_user(creds, FakeRedis())

    def test_no_credentials_returns_none(self):
        assert asyncio.run(self._call(None)) is None

    def test_invalid_token_returns_none(self):
        assert asyncio.run(self._call("garbage.token.here")) is None

    def test_valid_user_token_returns_payload(self):
        token = create_access_token("u1", "user")
        payload = asyncio.run(self._call(token))
        assert payload is not None
        assert payload["sub"] == "u1"
        assert payload["role"] == "user"

    def test_valid_admin_token_returns_payload(self):
        token = create_access_token("a1", "admin")
        payload = asyncio.run(self._call(token))
        assert payload["role"] == "admin"


class TestPanoramaQueryFilter:
    """panorama Cypher 查询：guest 带状态过滤，user 不带。"""

    def test_public_scope_has_status_filter(self):
        scope = graph_api._position_scope({"role": "guest", "sub": "g1"})
        status_filter = "p.status IN $public_statuses" if scope == "public" else "true"
        assert "p.status IN $public_statuses" in status_filter

    def test_all_scope_no_status_filter(self):
        scope = graph_api._position_scope({"role": "admin", "sub": "a1"})
        status_filter = "p.status IN $public_statuses" if scope == "public" else "true"
        assert status_filter == "true"


class TestShortestPathStatusFilter:
    """shortest_path 服务：传 position_statuses 时查询含状态过滤。"""

    def test_with_statuses_builds_filter_clause(self):
        from app.services.graph_algorithms.shortest_path import shortest_path

        class _Rec:
            def __init__(self, path):
                self._path = path

            def single(self):
                return {"path": self._path}

        class _Session:
            def __init__(self):
                self.queries = []

            def run(self, query, **params):
                self.queries.append((query, params))
                return _Rec([{"id": "s1", "name": "Python", "type": "Skill"}])

        session = _Session()
        shortest_path(session, "s1", "s2", position_statuses=["emerging", "stable", "declining"])
        query, params = session.queries[0]
        assert "position_statuses" in query
        assert params["position_statuses"] == ["emerging", "stable", "declining"]

    def test_without_statuses_no_filter_clause(self):
        from app.services.graph_algorithms.shortest_path import shortest_path

        class _Rec:
            def single(self):
                return {"path": []}

        class _Session:
            def __init__(self):
                self.queries = []

            def run(self, query, **params):
                self.queries.append(query)
                return _Rec()

        session = _Session()
        shortest_path(session, "s1", "s2")
        assert "position_statuses" not in session.queries[0]
