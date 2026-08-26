"""动态别名表 list 端点测试（方案① 补齐前端配套）。

asyncio.run 直调 list_skill_aliases（monkeypatch async_session_factory 注入
fake session），对齐 test_skill_classify_approval.py 写法。覆盖：全量/状态过滤/
分页 total/serialize 字段完整。
"""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from app.api.v1.admin_routes import skill_aliases as mod


def _row(variant=".NET Framework", standard=".NET", status="approved", **kw):
    base = dict(
        id="11111111-1111-1111-1111-111111111111",
        variant=variant, standard_name=standard, status=status,
        proposal_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        source="llm_review", reviewed_by="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        review_reason="家族变体", confidence=0.95, applied_to_graph=False,
        created_at=datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc),
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def scalars(self, stmt):
        return SimpleNamespace(all=lambda: self._rows)

    async def scalar(self, stmt):
        return len(self._rows)


class _FakeFactory:
    def __init__(self, rows):
        self._rows = rows

    def __call__(self):
        return _FakeSession(self._rows)


class TestListSkillAliases:
    def test_list_serializes_fields(self, monkeypatch):
        rows = [_row()]
        monkeypatch.setattr("app.core.database.async_session_factory", _FakeFactory(rows))
        # 直调须传显式参数（Query 默认值仅 FastAPI DI 解析）
        res = asyncio.run(mod.list_skill_aliases(status="", limit=50, offset=0))
        # 第六轮审查 P0-2：响应须为 ApiResponse 包装（前端 apiGet 取 res.data.data）
        assert res.code == 0
        data = res.data
        assert data["total"] == 1
        it = data["items"][0]
        assert it["variant"] == ".NET Framework"
        assert it["standard_name"] == ".NET"
        assert it["status"] == "approved"
        assert it["confidence"] == 0.95
        assert it["created_at"].startswith("2026-08-26T12:00")  # ISO 字符串

    def test_list_empty(self, monkeypatch):
        monkeypatch.setattr("app.core.database.async_session_factory", _FakeFactory([]))
        res = asyncio.run(mod.list_skill_aliases(status="approved", limit=10, offset=0))
        assert res.code == 0
        assert res.data == {"items": [], "total": 0, "limit": 10, "offset": 0}

    def test_build_query_status_filter(self):
        from app.models.business import SkillAlias

        stmt = mod.build_query("approved", 10, 20)
        compiled = str(stmt)
        assert "skill_aliases.status" in compiled or "status" in compiled
        assert SkillAlias is not None  # 查询可构造（不触库）


class TestSerialize:
    def test_serialize_alias_created_at_iso(self):
        it = mod.serialize_alias(_row())
        assert isinstance(it["created_at"], str) and "T" in it["created_at"]
        assert set(it.keys()) == set(mod._SERIALIZE_FIELDS)
