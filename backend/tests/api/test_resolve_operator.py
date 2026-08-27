"""resolve_operator 公共守卫测试（第六轮审查 P1-4：operator UUID 守卫统一）。

AuditLog.user_id 为 UUID 列：sub 缺失/非 UUID 时此前各端点行为不一（部分
fallback "admin" 撞列约束 500）。统一守卫后：合法 UUID 透过，非法返回
422/4000 错误响应。
"""

from app.api.common import resolve_operator

_UUID = "0356249f-9b04-47a3-a307-af6e7883f084"


class TestResolveOperator:
    def test_valid_sub_passes(self):
        operator, err = resolve_operator({"sub": _UUID})
        assert err is None and operator == _UUID

    def test_missing_sub_falls_back_then_rejected(self):
        """sub 缺失 → fallback 'admin' → 非 UUID，须显式 422 而非 500。"""
        operator, err = resolve_operator({})
        assert operator is None
        assert err is not None and err.status_code == 422

    def test_non_uuid_sub_rejected(self):
        operator, err = resolve_operator({"sub": "not-a-uuid"})
        assert operator is None and err is not None

    def test_user_id_fallback_used_when_valid(self):
        operator, err = resolve_operator({"user_id": _UUID})
        assert err is None and operator == _UUID
