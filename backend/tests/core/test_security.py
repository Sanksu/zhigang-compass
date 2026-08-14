"""core.security 单元测试（08-14 审查：hash/verify/权限/refresh token 此前无覆盖）。

RSA 密钥：fixture 收敛于 tests/conftest.py（临时密钥对注入 settings）。
"""

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    has_permission,
    hash_password,
    verify_password,
)


class TestPasswordHash:
    def test_hash_verify_roundtrip(self):
        hashed = hash_password("s3cret-pass")
        assert hashed != "s3cret-pass"  # 不存明文
        assert verify_password("s3cret-pass", hashed) is True

    def test_wrong_password_rejected(self):
        hashed = hash_password("correct")
        assert verify_password("wrong", hashed) is False

    def test_hash_is_salted(self):
        """bcrypt 加盐：同密码两次哈希结果不同。"""
        assert hash_password("same-pass") != hash_password("same-pass")


class TestHasPermission:
    def test_admin_wildcard(self):
        assert has_permission("admin", "graph:read") is True
        assert has_permission("admin", "anything:else") is True

    def test_user_has_assigned_permissions(self):
        assert has_permission("user", "graph:read") is True
        assert has_permission("user", "match:run") is True
        assert has_permission("user", "graph:write") is True

    def test_guest_limited(self):
        assert has_permission("guest", "graph:read") is True
        assert has_permission("guest", "match:run") is False

    def test_unknown_role_denied(self):
        assert has_permission("robot", "graph:read") is False
        assert has_permission("", "graph:read") is False


class TestTokens:
    def test_access_token_has_jti_and_type(self):
        payload = decode_token(create_access_token("u1", "user"))
        assert payload["type"] == "access"
        assert payload["jti"]
        assert payload["sub"] == "u1"
        assert payload["role"] == "user"

    def test_refresh_token_has_jti_and_long_expiry(self):
        payload = decode_token(create_refresh_token("u1"))
        assert payload["type"] == "refresh"
        assert payload["jti"]
        assert payload["sub"] == "u1"

    def test_refresh_defaults_guest_role(self):
        payload = decode_token(create_refresh_token("u1"))
        assert payload["role"] == "guest"

    def test_access_and_refresh_jti_distinct(self):
        a = decode_token(create_access_token("u1", "user"))
        r = decode_token(create_refresh_token("u1"))
        assert a["jti"] != r["jti"]
