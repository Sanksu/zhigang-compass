"""JD 原始数据管理端点测试（jd_admin，第七轮审查 P1-7 补测）。

raw 数据编辑/删除属安全红线：覆盖列表映射 / 详情 / 更新（content_hash
重算 + AuditLog + resolve_operator 守卫）/ 删除 / 请求体校验守卫。
项目纯函数风格：asyncio.run 直调端点协程，注入 fake async session。
"""

import asyncio
import hashlib
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.api.v1.admin_routes import jd_admin as mod
from app.services.extraction.position_normalization import POSITION_NORMALIZATION_VERSION
from app.workers.etl_tasks import _build_jd_text

import json


def _resp_code(resp):
    """error() 返回 JSONResponse：取业务码。"""
    return json.loads(resp.body)["code"]



def _row(jd_id: int = 42, **snapshot_extra) -> SimpleNamespace:
    return SimpleNamespace(
        id=jd_id,
        snapshot={
            "title": "Python 开发工程师",
            "company": "某科技",
            "location": "北京",
            "experience": "3-5年",
            "normalized_position": "Python 开发工程师",
            "normalized_position_meta": {"version": POSITION_NORMALIZATION_VERSION},
            "extraction": {"salary_range": "1-1.3万", "education": {"level": "本科"}},
            **snapshot_extra,
        },
        raw_text="岗位职责：负责后端服务开发与维护。",
        source="zhilian",
        source_id="zhilian-42",
        source_url="https://example.com/jd/42",
        crawled_at="2026-08-28 10:00:00",
        is_desensitized=False,
        content_hash="old-hash",
        updated_at=datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc),
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


class TestListJd:
    def test_列表映射与分页字段(self):
        row = _row()
        db = _FakeSession([_FakeResult(one=1), _FakeResult(rows=[row])])
        resp = asyncio.run(mod.list_jd(
            q="", source="", page=1, size=20, db=db, current_user=_ADMIN,
        ))
        data = resp.data
        assert data["total"] == 1 and data["page"] == 1 and data["size"] == 20
        item = data["items"][0]
        assert item["id"] == 42
        assert item["title"] == "Python 开发工程师"
        assert item["position"] == "Python 开发工程师"
        assert item["text_length"] == len(row.raw_text)
        assert item["updated_at"].startswith("2026-08-29T12:00")

    def test_空列表返回空态结构(self):
        db = _FakeSession([_FakeResult(one=0), _FakeResult(rows=[])])
        resp = asyncio.run(mod.list_jd(
            q="", source="", page=1, size=20, db=db, current_user=_ADMIN,
        ))
        assert resp.data == {"total": 0, "page": 1, "size": 20, "items": []}


class TestGetJd:
    def test_存在返回详情字段(self):
        db = _FakeSession([_FakeResult(one_or_none=_row())])
        resp = asyncio.run(mod.get_jd(jd_id=42, db=db, current_user=_ADMIN))
        detail = resp.data
        assert detail["id"] == 42
        assert detail["raw_text"].startswith("岗位职责")
        assert detail["extraction_summary"]["salary_range"] == "1-1.3万"
        assert detail["extraction_summary"]["education_level"] == "本科"

    def test_不存在返回404(self):
        db = _FakeSession([_FakeResult(one_or_none=None)])
        resp = asyncio.run(mod.get_jd(jd_id=999, db=db, current_user=_ADMIN))
        assert _resp_code(resp) == 4040


class TestUpdateJd:
    def test_正文变更重算content_hash并写审计(self):
        row = _row()
        db = _FakeSession([_FakeResult(one_or_none=row)])
        body = mod.JdAdminUpdateIn(raw_text="全新正文内容")
        resp = asyncio.run(mod.update_jd(jd_id=42, body=body, db=db, current_user=_ADMIN))

        expected_hash = hashlib.sha256(
            (_build_jd_text(row.snapshot, "全新正文内容") or "").encode("utf-8")
        ).hexdigest()
        assert row.content_hash == expected_hash
        assert row.raw_text == "全新正文内容"
        assert db.committed
        assert len(db.added) == 1
        assert db.added[0].action == "admin.jd.update"
        assert db.added[0].user_id == _ADMIN["sub"]
        assert resp.data["raw_text"] == "全新正文内容"

    def test_标题变更也触发hash重算(self):
        row = _row()
        db = _FakeSession([_FakeResult(one_or_none=row)])
        body = mod.JdAdminUpdateIn(title="Java 开发工程师")
        asyncio.run(mod.update_jd(jd_id=42, body=body, db=db, current_user=_ADMIN))
        assert row.snapshot["title"] == "Java 开发工程师"
        assert row.content_hash != "old-hash"

    def test_无变更不写审计不提交(self):
        row = _row()
        db = _FakeSession([_FakeResult(one_or_none=row)])
        body = mod.JdAdminUpdateIn(title="Python 开发工程师")  # 与现值相同
        resp = asyncio.run(mod.update_jd(jd_id=42, body=body, db=db, current_user=_ADMIN))
        assert not db.added and not db.committed
        assert row.content_hash == "old-hash"
        assert resp.data["id"] == 42

    def test_操作者非UUID返回校验错误不落库(self):
        row = _row()
        db = _FakeSession([_FakeResult(one_or_none=row)])
        body = mod.JdAdminUpdateIn(raw_text="x")
        resp = asyncio.run(mod.update_jd(
            jd_id=42, body=body, db=db,
            current_user={"sub": "not-a-uuid", "role": "admin"},
        ))
        assert _resp_code(resp) == 4000  # resolve_operator 守卫复用统一文案的业务码
        assert not db.committed and not db.added
        assert row.raw_text == "岗位职责：负责后端服务开发与维护。"

    def test_不存在返回404(self):
        db = _FakeSession([_FakeResult(one_or_none=None)])
        resp = asyncio.run(mod.update_jd(
            jd_id=999, body=mod.JdAdminUpdateIn(title="x"), db=db, current_user=_ADMIN,
        ))
        assert _resp_code(resp) == 4040


class TestDeleteJd:
    def test_删除写审计并提交(self):
        row = _row()
        db = _FakeSession([_FakeResult(one_or_none=row)])
        resp = asyncio.run(mod.delete_jd(jd_id=42, db=db, current_user=_ADMIN))
        assert db.deleted == [row] and db.committed
        assert db.added[0].action == "admin.jd.delete"
        assert db.added[0].detail["source"] == "zhilian"
        assert resp.data == {"deleted": True, "id": 42}

    def test_删除操作者非UUID不落库(self):
        row = _row()
        db = _FakeSession([_FakeResult(one_or_none=row)])
        resp = asyncio.run(mod.delete_jd(
            jd_id=42, db=db, current_user={"sub": "admin", "role": "admin"},
        ))
        assert _resp_code(resp) == 4000
        assert not db.deleted and not db.committed


class TestJdAdminUpdateIn:
    def test_crawled_at非法格式拒绝(self):
        with pytest.raises(ValidationError):
            mod.JdAdminUpdateIn(crawled_at="garbage")

    def test_crawled_at合法格式通过(self):
        assert mod.JdAdminUpdateIn(crawled_at="2026-08-29 12:00:00").crawled_at

    def test_source_url仅接受http_s(self):
        with pytest.raises(ValidationError):
            mod.JdAdminUpdateIn(source_url="javascript:alert(1)")
        assert mod.JdAdminUpdateIn(source_url="https://example.com").source_url

    def test_空串表示显式清空并通过校验(self):
        body = mod.JdAdminUpdateIn(source_url="", crawled_at="")
        assert body.source_url == "" and body.crawled_at == ""

    def test_字段超长拒绝(self):
        with pytest.raises(ValidationError):
            mod.JdAdminUpdateIn(title="长" * 201)
