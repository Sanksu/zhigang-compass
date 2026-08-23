"""依赖服务异常按契约发射错误码（设计文档 §2.4.7）。

覆盖：
- 未捕获 Neo4j 异常（GqlError 族）→ 5001/500
- pgvector 查询失败 → PgvectorUnavailableError，全局兜底 5002/500
- LLM 超时（match 诊断端点）→ 5003/504
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


# ---------- 5003：LLM 超时（match 诊断端点） ----------


def test_match_diagnosis_llm_timeout_emits_5003():
    """LLM 超时 → 诊断端点返回 5003/504（而非旧契约 503/200）。"""
    from app.services.extraction.llm_provider import LLMTimeoutError

    snapshot = {
        "gaps": [{"skill": "Python", "status": "missing"}],
        "position_name": "",  # 空岗位名跳过 RAG 检索，聚焦 LLM 异常路径
    }
    with patch.object(match_api, "_load_match_result", new=AsyncMock(return_value=snapshot)), \
         patch.object(match_api, "redis_client", new=AsyncMock()) as redis_mock, \
         patch("app.services.diagnosis.generator.generate_diagnosis",
               side_effect=LLMTimeoutError("provider timeout")) as gen_mock:
        redis_mock.get.return_value = None
        resp = asyncio.run(
            match_api.match_diagnosis(match_id="m1", user={"sub": "u1"})
        )

    gen_mock.assert_called_once()
    assert resp.status_code == 504
    assert json.loads(resp.body)["code"] == 5003


def test_match_diagnosis_llm_config_error_keeps_503():
    """LLM 配置不可用（非超时）→ 维持 503 语义。"""
    from app.services.extraction.llm_provider import LLMConfigurationError

    snapshot = {"gaps": [{"skill": "Python"}], "position_name": ""}
    with patch.object(match_api, "_load_match_result", new=AsyncMock(return_value=snapshot)), \
         patch.object(match_api, "redis_client", new=AsyncMock()) as redis_mock, \
         patch("app.services.diagnosis.generator.generate_diagnosis",
               side_effect=LLMConfigurationError("no api_key")):
        redis_mock.get.return_value = None
        resp = asyncio.run(
            match_api.match_diagnosis(match_id="m1", user={"sub": "u1"})
        )

    assert resp.status_code == 503


def test_match_diagnosis_llm_all_providers_failed_emits_503():
    """全部 provider 失败（父类 LLMExtractionError）→ 503（契约：LLM 不可用或超时），
    而非 500。"""
    from app.services.extraction.llm_provider import LLMExtractionError

    snapshot = {"gaps": [{"skill": "Python"}], "position_name": ""}
    with patch.object(match_api, "_load_match_result", new=AsyncMock(return_value=snapshot)), \
         patch.object(match_api, "redis_client", new=AsyncMock()) as redis_mock, \
         patch("app.services.diagnosis.generator.generate_diagnosis",
               side_effect=LLMExtractionError("所有 provider 均失败")):
        redis_mock.get.return_value = None
        resp = asyncio.run(
            match_api.match_diagnosis(match_id="m1", user={"sub": "u1"})
        )

    assert resp.status_code == 503
