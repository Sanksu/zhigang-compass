"""batch_extract LLM 全败延迟重试测试（设计文档 §6.5 延迟队列 + 评估报告 P0）。

- LLM 配置且全部条目 method=rule → 预算内 raise arq Retry(defer)，不落库不 commit
- 重试预算耗尽（job_try > _LLM_RETRY_MAX_JOB_TRY）→ 落库降级产物 + webhook 告警
- LLM 未配置（无 key 环境）→ rule 是设计内模式，不重试不告警
- 局部降级（llm/rule 混合）→ 正常落库，仅记 llm_rule_fallback 计数

mock 数据库层与 JDExtractor（对齐 test_batch_extract_retry 模式），
batch_extract 编排逻辑为真实产品代码。
"""

import asyncio
import unittest.mock as mock
from types import SimpleNamespace

import pytest
from arq.worker import Retry

from app.services.extraction.schemas import JDExtractionResult, SkillExtracted
from app.workers.etl_tasks import _LLM_RETRY_MAX_JOB_TRY, _llm_total_outage
from app.workers.tasks import batch_extract


def _jd_row(jd_id, raw_text=None):
    return SimpleNamespace(
        id=jd_id,
        snapshot={},
        source="boss",
        source_url="http://x/jd",
        crawled_at="2026-08-23T00:00:00+08:00",
        raw_text=raw_text or ("岗位描述" * 20),
    )


def _extraction(method="rule", position_name="Python 开发工程师"):
    return JDExtractionResult(
        position_name=position_name,
        skills=[SkillExtracted(name="Python")],
        method=method,
    )


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def scalars(self, stmt):
        return _FakeResult(self._rows)

    async def commit(self):
        self.committed = True


class _FakeNeo4jSession:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeDriver:
    def session(self):
        return _FakeNeo4jSession()


class _SentinelLLM:
    pass


def _run(rows, methods, llm_configured=True):
    """patch 数据库层 + 抽取器后执行 batch_extract；返回 (结果, session, 告警列表)。

    methods 与行数等长：每条抽取结果的 method 值。
    """

    class _FakeExtractor:
        llm = _SentinelLLM() if llm_configured else None

        def extract_batch(self, texts, **kwargs):
            return [_extraction(method=m) for m in methods[: len(texts)]]

    alerts: list[tuple] = []

    async def _fake_alert(event, message):
        alerts.append((event, message))

    session = _FakeSession(rows)

    with (
        mock.patch("app.core.database.async_session_factory", return_value=session),
        mock.patch("app.core.database.neo4j_driver", _FakeDriver()),
        mock.patch(
            "app.services.extraction.jd_extractor.JDExtractor",
            return_value=_FakeExtractor(),
        ),
        mock.patch("app.services.kg.kg_service.import_jd", return_value="pos_0001"),
        mock.patch("app.services.alerting.send_alert", side_effect=_fake_alert),
    ):
        result = asyncio.run(batch_extract({}, limit=100))
    return result, session, alerts


class TestLlmTotalOutageHelper:
    def test_all_rule_with_llm_configured_is_outage(self):
        assert _llm_total_outage(["rule", "rule"], llm_configured=True) is True

    def test_llm_not_configured_is_not_outage(self):
        # 无 key 环境规则抽取是设计内模式
        assert _llm_total_outage(["rule", "rule"], llm_configured=False) is False

    def test_mixed_methods_not_outage(self):
        assert _llm_total_outage(["llm", "rule"], llm_configured=True) is False

    def test_all_llm_not_outage(self):
        assert _llm_total_outage(["llm"], llm_configured=True) is False

    def test_empty_not_outage(self):
        assert _llm_total_outage([], llm_configured=True) is False


class TestDeferredRetryOnTotalOutage:
    def test_budget_remaining_raises_retry_without_commit(self):
        """预算内：raise Retry(defer)，会话回滚、extraction 不落库、无告警。"""
        rows = [_jd_row(1), _jd_row(2)]

        class _FakeExtractor:
            llm = _SentinelLLM()

            def extract_batch(self, texts, **kwargs):
                return [_extraction(method="rule") for _ in texts]

        session = _FakeSession(rows)
        alerts: list[tuple] = []

        async def _fake_alert(event, message):
            alerts.append((event, message))

        with (
            mock.patch("app.core.database.async_session_factory", return_value=session),
            mock.patch("app.core.database.neo4j_driver", _FakeDriver()),
            mock.patch(
                "app.services.extraction.jd_extractor.JDExtractor",
                return_value=_FakeExtractor(),
            ),
            mock.patch("app.services.kg.kg_service.import_jd", return_value="pos_0001"),
            mock.patch("app.services.alerting.send_alert", side_effect=_fake_alert),
        ):
            with pytest.raises(Retry):
                asyncio.run(batch_extract({}, limit=100))

        assert session.committed is False
        assert "extraction" not in rows[0].snapshot
        assert alerts == []

    @pytest.mark.parametrize("job_try", [1, 2])
    def test_each_try_within_budget_defers(self, job_try):
        rows = [_jd_row(1)]

        class _FakeExtractor:
            llm = _SentinelLLM()

            def extract_batch(self, texts, **kwargs):
                return [_extraction(method="rule") for _ in texts]

        with (
            mock.patch("app.core.database.async_session_factory", return_value=_FakeSession(rows)),
            mock.patch("app.core.database.neo4j_driver", _FakeDriver()),
            mock.patch(
                "app.services.extraction.jd_extractor.JDExtractor",
                return_value=_FakeExtractor(),
            ),
            mock.patch("app.services.kg.kg_service.import_jd", return_value="pos_0001"),
        ):
            with pytest.raises(Retry) as exc_info:
                asyncio.run(batch_extract({"job_try": job_try}, limit=100))
        # arq 0.28：Retry.defer 以毫秒存于 defer_score（zset score 延迟重跑）
        assert exc_info.value.defer_score == 600_000
        assert job_try <= _LLM_RETRY_MAX_JOB_TRY

    def test_budget_exhausted_persists_degraded_and_alerts(self):
        """预算耗尽：落库 method=rule 产物 + webhook 告警。"""
        rows = [_jd_row(1)]
        job_try = _LLM_RETRY_MAX_JOB_TRY + 1

        class _FakeExtractor:
            llm = _SentinelLLM()

            def extract_batch(self, texts, **kwargs):
                return [_extraction(method="rule") for _ in texts]

        session2 = _FakeSession(rows)
        alerts2: list[tuple] = []

        async def _fake_alert(event, message):
            alerts2.append((event, message))

        with (
            mock.patch("app.core.database.async_session_factory", return_value=session2),
            mock.patch("app.core.database.neo4j_driver", _FakeDriver()),
            mock.patch(
                "app.services.extraction.jd_extractor.JDExtractor",
                return_value=_FakeExtractor(),
            ),
            mock.patch("app.services.kg.kg_service.import_jd", return_value="pos_0001"),
            mock.patch("app.services.alerting.send_alert", side_effect=_fake_alert),
        ):
            result2 = asyncio.run(batch_extract({"job_try": job_try}, limit=100))

        assert session2.committed is True
        assert rows[0].snapshot["extraction"]["method"] == "rule"
        assert result2["llm_rule_fallback"] == 1
        assert len(alerts2) == 1
        event, message = alerts2[0]
        assert event == "batch_extract_llm_degraded"
        assert "规则抽取降级" in message

    def test_llm_unconfigured_rule_mode_no_retry_no_alert(self):
        """无 key 环境：全部 rule 不算事故，正常落库无告警。"""
        rows = [_jd_row(1)]

        class _FakeExtractor:
            llm = None

            def extract_batch(self, texts, **kwargs):
                return [_extraction(method="rule") for _ in texts]

        session = _FakeSession(rows)
        alerts: list[tuple] = []

        async def _fake_alert(event, message):
            alerts.append((event, message))

        with (
            mock.patch("app.core.database.async_session_factory", return_value=session),
            mock.patch("app.core.database.neo4j_driver", _FakeDriver()),
            mock.patch(
                "app.services.extraction.jd_extractor.JDExtractor",
                return_value=_FakeExtractor(),
            ),
            mock.patch("app.services.kg.kg_service.import_jd", return_value="pos_0001"),
            mock.patch("app.services.alerting.send_alert", side_effect=_fake_alert),
        ):
            result = asyncio.run(batch_extract({}, limit=100))

        assert session.committed is True
        assert alerts == []
        assert result["succeeded"] == 1
        assert rows[0].snapshot["extraction"]["method"] == "rule"


class TestPartialDegradation:
    def test_mixed_results_persist_and_count_fallback(self):
        """混合场景：正常落库，仅记录 llm_rule_fallback 计数。"""
        rows = [_jd_row(1), _jd_row(2)]
        result, session, alerts = _run(rows, ["llm", "rule"])

        assert session.committed is True
        assert result["succeeded"] == 2
        assert result["llm_rule_fallback"] == 1
        assert alerts == []
