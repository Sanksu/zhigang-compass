"""简历原始文件 DB 留存与下载单元测试（设计文档 §8.1 文件解析）。

用 FakeDb 桩 + 直接调函数风格（参考 test_match_persistence.py）覆盖：
- 上传落库写 ResumeFile 行（纯函数字段断言 + parse_resume 端点整体，
  含命中解析缓存分支不重复写行）
- 下载所有者校验（本人可下载 / 他人 403 / 不存在 404 / 非法 id 400）
- 删除简历联动删除 ResumeFile 行
均不触真实数据库。
"""

import asyncio
import io
import json
from uuid import uuid4

from fastapi import UploadFile
from fastapi.responses import Response
from sqlalchemy.sql.dml import Delete

import app.api.v1.resume as resume_mod
from app.api.v1.resume import (
    _download_disposition,
    _persist_resume_file,
    delete_resume,
    download_resume_file,
    parse_resume,
)
from app.models.business import ResumeCache, ResumeFile, TaskStatus

_RID = "a3b7f0d2-2d5a-4e1c-8f6b-1c3d5e7f9a0b"
_BODY = b"%PDF-1.4 test"


class _FakeDb:
    """假 AsyncSession：scalar/get 返回注入行（无则 None），add/delete/execute 记录。

    flush 为缺 id 的 added 对象补 UUID（模拟 SQLAlchemy 列默认值），
    使 parse_resume 在写 ResumeFile 前能取到 task.id。
    """

    def __init__(self, rows=None, stored=None, scalar_returns=None):
        self._rows = rows or []
        self._stored = stored or {}
        # scalar_returns：按调用顺序逐个返回的 scalar 结果（如 delete 的
        # 归属校验返回行、随后 other_owner 查询返回 None 的场景）
        self._scalar_returns = list(scalar_returns or [])
        self.added = []
        self.deleted = []
        self.executed = []
        self.commits = 0

    async def scalar(self, stmt):
        if self._scalar_returns:
            return self._scalar_returns.pop(0)
        return self._rows[0] if self._rows else None

    async def get(self, model, pk):
        return self._stored.get(pk)

    async def execute(self, stmt):
        self.executed.append(stmt)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = str(uuid4())

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        return obj


class TestPersistResumeFile:
    def test_writes_row_with_fields(self):
        """上传落库：resume_id/user_id/字节内容/元信息逐字段写入 ResumeFile。"""

        async def _run():
            db = _FakeDb()
            await _persist_resume_file(
                db,
                resume_id="resume-1",
                user_id="u1",
                file_hash="abc123",
                file_name="简历.pdf",
                content_type="application/pdf",
                content=_BODY,
            )
            assert len(db.added) == 1
            row = db.added[0]
            assert isinstance(row, ResumeFile)
            assert row.resume_id == "resume-1"
            assert row.user_id == "u1"
            assert row.file_hash == "abc123"
            assert row.file_name == "简历.pdf"
            assert row.content_type == "application/pdf"
            assert row.content == _BODY
            assert row.file_size == len(_BODY)

        asyncio.run(_run())


class TestUploadPersistsFile:
    @staticmethod
    def _upload() -> UploadFile:
        # starlette 1.3.1 UploadFile.read 对 BytesIO 走内存直读路径
        return UploadFile(
            filename="简历.pdf",
            file=io.BytesIO(_BODY),
            headers={"content-type": "application/pdf"},
        )

    def test_parse_writes_resume_file_row(self, monkeypatch, tmp_path):
        """端点整体：上传 → 同事务写 ResumeFile 行（resume_id=任务 id，user_id=JWT sub）。"""

        async def _run():
            enqueued = []

            async def fake_enqueue(file_path, task_id):
                enqueued.append((file_path, task_id))

            monkeypatch.setattr(resume_mod, "_UPLOAD_DIR", tmp_path)
            monkeypatch.setattr(resume_mod, "_enqueue_resume_parse", fake_enqueue)
            db = _FakeDb()

            resp = await parse_resume(self._upload(), db, {"sub": "u1", "role": "user"})

            assert resp.data["cached"] is False
            task = next(o for o in db.added if isinstance(o, TaskStatus))
            row = next(o for o in db.added if isinstance(o, ResumeFile))
            assert row.resume_id == str(task.id)
            assert row.user_id == "u1"
            assert row.content == _BODY
            assert row.file_name == "简历.pdf"
            assert row.content_type == "application/pdf"
            assert row.file_size == len(_BODY)
            assert len(enqueued) == 1

        asyncio.run(_run())

    def test_cache_hit_skips_resume_file_row(self):
        """命中解析缓存：直接复用，不写 ResumeFile 行、不新建任务。"""

        async def _run():
            cached = ResumeCache(file_hash="h", file_name="a.pdf", parsed_data={})
            db = _FakeDb(rows=[cached])

            resp = await parse_resume(self._upload(), db, {"sub": "u1"})

            assert resp.data["cached"] is True
            assert db.added == []

        asyncio.run(_run())


class TestDownloadDisposition:
    def test_ascii_filename(self):
        assert _download_disposition("resume.pdf") == 'attachment; filename="resume.pdf"'

    def test_cjk_filename_uses_rfc5987(self):
        # 中文文件名不能安全放进引号，走 RFC 5987 filename*（与 starlette FileResponse 同格式）
        assert _download_disposition("简历.pdf") == "attachment; filename*=utf-8''%E7%AE%80%E5%8E%86.pdf"


class TestDownloadResumeFile:
    @staticmethod
    def _row(user_id: str = "u1") -> ResumeFile:
        return ResumeFile(
            resume_id=_RID,
            user_id=user_id,
            file_hash="h",
            file_name="简历.pdf",
            content_type="application/pdf",
            content=_BODY,
            file_size=len(_BODY),
        )

    def test_owner_can_download(self):
        """本人（JWT sub 匹配 user_id）→ 返回字节响应，media_type 与附件文件名正确。"""

        async def _run():
            db = _FakeDb(rows=[self._row(user_id="u1")])
            resp = await download_resume_file(_RID, db, {"sub": "u1"})
            assert isinstance(resp, Response)
            assert resp.media_type == "application/pdf"
            assert resp.body == _BODY
            assert "attachment" in resp.headers["content-disposition"]
            assert "filename*=utf-8''" in resp.headers["content-disposition"]

        asyncio.run(_run())

    def test_non_owner_forbidden(self):
        """他人下载 → 4030 + HTTP 403（仅本人可下载，管理员也无权访问原文）。"""

        async def _run():
            db = _FakeDb(rows=[self._row(user_id="u1")])
            resp = await download_resume_file(_RID, db, {"sub": "u2"})
            assert resp.status_code == 403
            assert json.loads(resp.body)["code"] == 4030

        asyncio.run(_run())

    def test_missing_file_404(self):
        async def _run():
            db = _FakeDb()  # 无行
            resp = await download_resume_file(_RID, db, {"sub": "u1"})
            assert resp.status_code == 404
            assert json.loads(resp.body)["code"] == 4040

        asyncio.run(_run())

    def test_invalid_id_400(self):
        async def _run():
            db = _FakeDb()
            resp = await download_resume_file("not-a-uuid", db, {"sub": "u1"})
            assert resp.status_code == 400
            assert json.loads(resp.body)["code"] == 400

        asyncio.run(_run())


class TestDeleteResume:
    def test_deletes_cache_and_resume_file(self, monkeypatch, tmp_path):
        """删除联动：删除 resume_cache 记录的同时按 resume_id 删除 resume_files 行。"""

        async def _run():
            resume = ResumeCache(id=_RID, file_hash="h", file_name="a.pdf", parsed_data={})
            # 归属校验需命中本人 ResumeFile 行；随后 other_owner 查询返回 None
            # （无其他用户引用该缓存），流程走到删除缓存 + 落盘文件
            db = _FakeDb(
                stored={_RID: resume},
                scalar_returns=[
                    ResumeFile(
                        resume_id=_RID, user_id="u1", file_hash="h", file_name="a.pdf",
                        content_type="application/pdf", content=_BODY, file_size=len(_BODY),
                    ),
                    None,
                ],
            )
            monkeypatch.setattr(resume_mod, "_UPLOAD_DIR", tmp_path)

            resp = await delete_resume(_RID, db, {"sub": "u1"})

            # 契约 DELETE /resume/{id} 为 204 无响应体，前端仅据状态码判断成功
            assert resp.status_code == 204
            assert db.deleted == [resume]
            assert db.commits == 1
            assert len(db.executed) == 1
            stmt = db.executed[0]
            assert isinstance(stmt, Delete)
            assert stmt.table.name == "resume_files"

        asyncio.run(_run())
