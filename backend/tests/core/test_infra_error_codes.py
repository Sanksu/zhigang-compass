"""依赖服务异常按契约发射错误码（设计文档 §2.4.7）。

覆盖：
- 未捕获 Neo4j 异常（GqlError 族）→ 5001/500
- pgvector 查询失败 → PgvectorUnavailableError，全局兜底 5002/500
- match 诊断 GET 只读化：Redis 命中 / PG 耐久回退 / 皆无 404（08-23 闭环收敛）
- vector_store.load_* 对 SQLAlchemyError 转抛 PgvectorUnavailableError
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from neo4j.exceptions import ServiceUnavailable
from sqlalchemy.exc import ProgrammingError

from app import main as main_module
from app.api.v1 import match as match_api
from app.core.errors import ERR_VALIDATION, HTTP_STATUS_ERROR_CODE
from app.main import unhandled_exception_handler
from app.services.embeddings.vector_store import (
    PgvectorUnavailableError,
    load_project_vectors,
)


def _req():
    return SimpleNamespace(method="GET", url=SimpleNamespace(path="/api/v1/x"))


def _silence_logger():
    return patch.object(main_module.logger, "exception")


# ---------- 4000：HTTP 状态映射 ----------


def test_content_too_large_maps_to_validation_error():
    """413 请求内容过大 → 参数校验错误码 4000。"""
    from fastapi import status

    assert HTTP_STATUS_ERROR_CODE[status.HTTP_413_CONTENT_TOO_LARGE] == ERR_VALIDATION


# ---------- 5001：Neo4j 异常 ----------


def test_unhandled_neo4j_error_emits_5001():
    """Neo4j 驱动异常（ServiceUnavailable，属 GqlError 族）→ 5001/500。"""
    with _silence_logger():
        resp = asyncio.run(
            unhandled_exception_handler(_req(), ServiceUnavailable("Neo4j down"))
        )
    assert resp.status_code == 500
    assert json.loads(resp.body)["code"] == 5001


# ---------- 5002：pgvector 异常 ----------


def test_unhandled_pgvector_error_emits_5002():
    """pgvector 查询失败未降级路径 → 5002/500。"""
    with _silence_logger():
        resp = asyncio.run(
            unhandled_exception_handler(_req(), PgvectorUnavailableError("pg down"))
        )
    assert resp.status_code == 500
    assert json.loads(resp.body)["code"] == 5002


def test_other_exception_still_emits_5000():
    """普通未分类异常维持 5000，不回退到依赖错误码。"""
    with _silence_logger():
        resp = asyncio.run(unhandled_exception_handler(_req(), ValueError("boom")))
    assert resp.status_code == 500
    assert json.loads(resp.body)["code"] == 5000


class _FakeDbScalarsError:
    """mock AsyncSession.scalars 抛 SQLAlchemy 异常（表缺失场景）。"""

    async def scalars(self, *_a, **_k):
        raise ProgrammingError(
            "SELECT ...", {}, Exception("relation project_embeddings does not exist")
        )


def test_load_project_vectors_raises_pgvector_error():
    """pgvector 表缺失 → load_* 转抛 PgvectorUnavailableError（供全局兜底 5002）。"""
    with pytest.raises(PgvectorUnavailableError):
        asyncio.run(load_project_vectors(_FakeDbScalarsError(), "resume-id"))


# ---------- 诊断 GET 只读化：Redis → PG 耐久回退 → 404 ----------


class _FakeScalarDb:
    """GET 诊断端点的最小 AsyncSession 替身（仅 scalar 查询）。"""

    def __init__(self, row):
        self._row = row

    async def scalar(self, *_args, **_kwargs):
        return self._row


def test_match_diagnosis_readonly_cache_hit():
    """Redis 命中 → 直接返回报告，不查库不触发生成路径。"""
    snapshot = {"gaps": [{"skill": "Python", "status": "missing"}]}
    payload = {"match_id": "m1", "overall_summary": "ok"}
    with patch.object(match_api, "_load_match_result", new=AsyncMock(return_value=snapshot)),          patch.object(match_api, "redis_client", new=AsyncMock()) as redis_mock:
        redis_mock.get.return_value = json.dumps(payload)
        db = _FakeScalarDb(row=object())  # 不应被读取
        resp = asyncio.run(
            match_api.match_diagnosis(match_id="m1", db=db, user={"sub": "u1"})
        )

    assert resp.data["overall_summary"] == "ok"


def test_match_diagnosis_readonly_falls_back_to_pg_record():
    """Redis 过期 → DiagnosisReportRecord 回读并回填缓存（耐久镜像读取回退）。"""
    snapshot = {"gaps": [{"skill": "Python"}]}
    record = SimpleNamespace(report={"match_id": "m1", "overall_summary": "durable"})
    with patch.object(match_api, "_load_match_result", new=AsyncMock(return_value=snapshot)),          patch.object(match_api, "redis_client", new=AsyncMock()) as redis_mock:
        redis_mock.get.return_value = None
        db = _FakeScalarDb(row=record)
        resp = asyncio.run(
            match_api.match_diagnosis(match_id="m1", db=db, user={"sub": "u1"})
        )

    assert resp.data["overall_summary"] == "durable"
    redis_mock.set.assert_awaited_once()


def test_match_diagnosis_readonly_returns_404_when_absent():
    """缓存与落库皆无 → 404 引导先 POST；同步生成路径已删除（不触达 LLM）。"""
    snapshot = {"gaps": [{"skill": "Python"}]}
    with patch.object(match_api, "_load_match_result", new=AsyncMock(return_value=snapshot)),          patch.object(match_api, "redis_client", new=AsyncMock()) as redis_mock,          patch("app.services.diagnosis.generator.generate_diagnosis") as gen_mock:
        redis_mock.get.return_value = None
        db = _FakeScalarDb(row=None)
        resp = asyncio.run(
            match_api.match_diagnosis(match_id="m1", db=db, user={"sub": "u1"})
        )

    assert resp.status_code == 404
    gen_mock.assert_not_called()
