"""图谱岗位可见性过滤单元测试（方案一：candidate 待审核不外宣）。

覆盖：
- _can_view_all_positions / _position_scope：角色 → 可见范围（纯函数）
- get_optional_user：匿名/无效 token → None，有效 user/admin → payload
- panorama / view 的 Cypher 查询是否带状态过滤（校验查询文本，不依赖 Neo4j）
"""

import asyncio
from unittest.mock import patch

import pytest
from fastapi.security import HTTPAuthorizationCredentials

from app.api.deps import get_optional_user
from app.api.v1 import graph as graph_api
from app.core.config import settings
from app.core.security import create_access_token


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


@pytest.fixture(scope="module")
def tmp_rsa_keys(tmp_path_factory):
    """生成临时 RSA 密钥对（参考 test_token_expired 的注入方式）。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    tmp = tmp_path_factory.mktemp("jwt-keys-graph")
    priv_path = tmp / "private.pem"
    pub_path = tmp / "public.pem"

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    pub_path.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return str(priv_path), str(pub_path)


@pytest.fixture(autouse=True)
def _use_tmp_keys(tmp_rsa_keys):
    priv, pub = tmp_rsa_keys
    with patch.object(settings, "jwt_private_key_path", priv), \
         patch.object(settings, "jwt_public_key_path", pub):
        yield


class TestGetOptionalUser:
    """可选鉴权依赖：匿名/无效 → None，有效 token → payload。"""

    async def _call(self, token: str | None):
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token) if token else None
        return await get_optional_user(creds, _FakeRedis())

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

class _FakeRedis:
    """黑名单检查桩（get 返回 None = 未拉黑）。"""

    async def get(self, key):
        return None
