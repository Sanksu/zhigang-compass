"""课程/论文/社区原始数据管理端点测试（raw_admin，通用 /admin/raw/{raw_type}）。

覆盖：列表映射（含类型特有 extra）/ 未知类型 404 / 详情 / 更新（title/
raw_text/source_url + AuditLog + resolve_operator 守卫）/ 删除 / 请求体校验。
项目纯函数风格：asyncio.run 直调端点协程，注入 fake session。
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.api.v1.admin_routes import raw_admin as mod


def _resp_code(resp):
    return json.loads(resp.body)["code"]


def _row(raw_id: int = 42, **snapshot_extra) -> SimpleNamespace:
    return SimpleNamespace(
        id=raw_id,
        snapshot={
            "title": "2027年山西专升本系统督学班",
            "institution": "某学院",
            "quality": "0.82",
            "skills": ["C程序设计", "英语"],
            **snapshot_extra,
        },
        raw_text="课程简介：涵盖 C 程序设计与英语。",
        source="icourse163",
        source_id="course-42",
        source_url="https://www.icourse163.org/course/42",
        crawled_at="2026-08-30 10:00:00",
        is_desensitized=False,
        updated_at=datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc),
    )


class _FakeResult:
    def __init__(self, one=None, rows=None, one_or_none=...):
        self._one = one
        self._rows = rows if rows is not None else []
        self._one_or_none = one_or_none

    def scalar_one(self):
        return self._one

    def scalar_one_or_none(self):
        return None if self._one_or_none is ... else self._one_or_none

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.deleted = []
        self.committed = False

    async def execute(self, stmt):
        return self._results.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.committed = True


_ADMIN = {"sub": str(uuid.uuid4()), "role": "admin"}


class TestListRaw:
    def test_course列表映射与extra字段(self):
        row = _row()
        db = _FakeSession([_FakeResult(one=1), _FakeResult(rows=[row])])
        resp = asyncio.run(mod.list_raw(
            raw_type="course", q="", source="", page=1, size=20, db=db, current_user=_ADMIN,
        ))
        item = resp.data["items"][0]
        assert item["id"] == 42 and item["source"] == "icourse163"
        assert item["extra"]["quality"] == "0.82"
        assert item["extra"]["skills_count"] == 2
        assert item["extra"]["institution"] == "某学院"
        assert item["text_length"] == len(row.raw_text)

    def test_paper与community_extra形态(self):
        paper = _row(
            title="A Survey of LLM Agents", authors=["A", "B"], published="2026-08-01",
        )
        db = _FakeSession([_FakeResult(one=1), _FakeResult(rows=[paper])])
        resp = asyncio.run(mod.list_raw(
            raw_type="paper", q="", source="", page=1, size=20, db=db, current_user=_ADMIN,
        ))
        assert resp.data["items"][0]["extra"] == {"published": "2026-08-01", "authors_count": 2}

        community = _row(
            title="trending repo", stars=120, votes=None, trend_type="daily",
        )
        db2 = _FakeSession([_FakeResult(one=1), _FakeResult(rows=[community])])
        resp2 = asyncio.run(mod.list_raw(
            raw_type="community", q="", source="", page=1, size=20, db=db2, current_user=_ADMIN,
        ))
        assert resp2.data["items"][0]["extra"] == {"stars": 120, "votes": None, "trend_type": "daily"}

    def test_未知类型404(self):
        db = _FakeSession([])
        resp = asyncio.run(mod.list_raw(
            raw_type="jd", q="", source="", page=1, size=20, db=db, current_user=_ADMIN,
        ))
        assert _resp_code(resp) == 4040


class TestGetRaw:
    def test_存在返回详情含快照全量(self):
        row = _row()
        db = _FakeSession([_FakeResult(one_or_none=row)])
        resp = asyncio.run(mod.get_raw(raw_type="course", raw_id=42, db=db, current_user=_ADMIN))
        detail = resp.data
        assert detail["id"] == 42 and detail["raw_type"] == "course"
        assert detail["raw_text"].startswith("课程简介")
        assert detail["snapshot"]["institution"] == "某学院"

    def test_不存在返回404(self):
        db = _FakeSession([_FakeResult(one_or_none=None)])
        resp = asyncio.run(mod.get_raw(raw_type="course", raw_id=999, db=db, current_user=_ADMIN))
        assert _resp_code(resp) == 4040


class TestUpdateRaw:
    def test_标题与正文变更写审计并提交(self):
        row = _row()
        db = _FakeSession([_FakeResult(one_or_none=row)])
        body = mod.RawAdminUpdateIn(title="新标题", raw_text="全新正文")
        resp = asyncio.run(mod.update_raw(
            raw_type="course", raw_id=42, body=body, db=db, current_user=_ADMIN,
        ))
        assert row.snapshot["title"] == "新标题"
        assert row.raw_text == "全新正文"
        assert db.committed and len(db.added) == 1
        assert db.added[0].action == "admin.raw.update"
        assert db.added[0].resource == "raw_course"
        assert db.added[0].user_id == _ADMIN["sub"]
        assert sorted(resp.data["changed_fields"]) == ["raw_text", "title"]

    def test_无变更不提交(self):
        row = _row()
        db = _FakeSession([_FakeResult(one_or_none=row)])
        body = mod.RawAdminUpdateIn(title="2027年山西专升本系统督学班")  # 与现值相同
        resp = asyncio.run(mod.update_raw(
            raw_type="course", raw_id=42, body=body, db=db, current_user=_ADMIN,
        ))
        assert not db.added and not db.committed
        assert resp.data.get("unchanged") is True

    def test_操作者非UUID不落库(self):
        row = _row()
        db = _FakeSession([_FakeResult(one_or_none=row)])
        resp = asyncio.run(mod.update_raw(
            raw_type="course", raw_id=42, body=mod.RawAdminUpdateIn(title="x"),
            db=db, current_user={"sub": "not-a-uuid", "role": "admin"},
        ))
        assert _resp_code(resp) == 4000
        assert not db.committed and not db.added

    def test_不存在返回404(self):
        db = _FakeSession([_FakeResult(one_or_none=None)])
        resp = asyncio.run(mod.update_raw(
            raw_type="course", raw_id=999, body=mod.RawAdminUpdateIn(title="x"),
            db=db, current_user=_ADMIN,
        ))
        assert _resp_code(resp) == 4040


class TestDeleteRaw:
    def test_删除写审计并提交(self):
        row = _row()
        db = _FakeSession([_FakeResult(one_or_none=row)])
        resp = asyncio.run(mod.delete_raw(raw_type="course", raw_id=42, db=db, current_user=_ADMIN))
        assert db.deleted == [row] and db.committed
        assert db.added[0].action == "admin.raw.delete"
        assert db.added[0].detail["source"] == "icourse163"
        assert resp.data == {"deleted": True, "id": 42, "raw_type": "course"}

    def test_删除操作者非UUID不落库(self):
        row = _row()
        db = _FakeSession([_FakeResult(one_or_none=row)])
        resp = asyncio.run(mod.delete_raw(
            raw_type="course", raw_id=42, db=db,
            current_user={"sub": "admin", "role": "admin"},
        ))
        assert _resp_code(resp) == 4000
        assert not db.deleted and not db.committed


class TestRawAdminUpdateIn:
    def test_source_url仅接受http_s(self):
        with pytest.raises(ValidationError):
            mod.RawAdminUpdateIn(source_url="javascript:alert(1)")
        assert mod.RawAdminUpdateIn(source_url="https://example.com").source_url

    def test_空串表示显式清空(self):
        body = mod.RawAdminUpdateIn(source_url="")
        assert body.source_url == ""

    def test_字段超长拒绝(self):
        with pytest.raises(ValidationError):
            mod.RawAdminUpdateIn(title="长" * 501)
