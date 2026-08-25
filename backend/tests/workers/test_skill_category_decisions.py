"""技能分类接入决策信封测试（PR4a：skill_classify 决策记录 shadow 落档）。"""

import asyncio
import unittest.mock as mock
from types import SimpleNamespace

from app.services.extraction.skill_category_review import SkillCategorySuggestion
from app.workers import skill_category_review as worker_module
from app.workers.skill_category_review import skill_category_review_daily


class _FakeLLM:
    def __init__(self):
        self._providers = [{"name": "deepseek", "model": "deepseek-v4-flash"}]

    def extract_structured(self, prompt, response_model, system_prompt=None, timeout=None):
        return SkillCategorySuggestion(category="AI/机器学习", confidence=0.9, reason="r")


class _FakeNeo4jRun:
    def __init__(self):
        self.calls = []

    def __call__(self, query, **params):
        self.calls.append((query, params))
        return SimpleNamespace(single=lambda: None)


class _FakeNeo4jSession:
    def __init__(self):
        self.run = _FakeNeo4jRun()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeDriver:
    def __init__(self):
        self.session_obj = _FakeNeo4jSession()

    def session(self):
        return self.session_obj


def _run(rows, enabled=True, tmp_path=None, record_error=None):
    driver = _FakeDriver()
    fake_llm = _FakeLLM()
    persisted: list = []

    async def _fake_persist(record):
        if record_error is not None:
            raise record_error
        persisted.append(record)
        return "rec-1"

    with (
        mock.patch.object(worker_module, "_fetch_unclassified", return_value=rows),
        mock.patch("app.core.database.neo4j_driver", driver),
        mock.patch(
            "app.core.runtime_config.get",
            side_effect=lambda k, d=None: (
                enabled if k == "skill_category_review_enabled"
                else 20 if k == "skill_category_max_candidates" else d
            ),
        ),
        mock.patch("app.services.extraction.llm_provider.LLMProviderChain", return_value=fake_llm),
        mock.patch.object(worker_module, "_REPORT_DIR", tmp_path),
        mock.patch("app.services.llm_decision.persist_record", side_effect=_fake_persist),
    ):
        summary = asyncio.run(skill_category_review_daily({}))
    return summary, persisted, driver


class TestSkillClassifyDecisionRecord:
    def test_decision_record_persisted_as_shadow(self, tmp_path):
        rows = [{"name": "某新技能X9", "req_count": 0, "suggested_category": None}]
        summary, persisted, driver = _run(rows, tmp_path=tmp_path)

        assert summary["status"] == "ok"
        assert len(persisted) == 1
        record = persisted[0]
        assert record.domain == "skill_classify"
        assert record.status == "shadow"
        assert record.risk_tier == "R0"
        assert record.entity_id == "某新技能X9"
        assert record.structured_output["category"] == "AI/机器学习"
        assert record.confidence == 0.9
        assert record.provider == "deepseek"
        assert record.model == "deepseek-v4-flash"
        assert record.evidence_refs == [{"req_count": 0}]
        # 提议字段仍照常写图谱（决策记录是旁路审计，不改既有链路）
        write_query, write_params = [q for q in driver.session_obj.run.calls if "SET" in q[0]][0]
        assert "s.suggested_category" in write_query

    def test_llm_failed_no_record(self, tmp_path):
        rows = [{"name": "某新技能X9", "req_count": 0, "suggested_category": None}]
        with mock.patch(
            "app.services.extraction.skill_category_review.classify_skill", return_value=None,
        ):
            summary, persisted, _ = _run(rows, tmp_path=tmp_path)
        assert summary["llm_failed"] == 1
        assert persisted == []

    def test_record_persist_failure_does_not_break_suggestion(self, tmp_path):
        rows = [{"name": "某新技能X9", "req_count": 0, "suggested_category": None}]
        summary, _, driver = _run(
            rows, tmp_path=tmp_path, record_error=RuntimeError("pg down"),
        )
        assert summary["classified"][0]["name"] == "某新技能X9"
        assert summary["record_failed"] == 1
        # 图谱提议写入不受影响
        write_queries = [q for q in driver.session_obj.run.calls if "SET" in q[0]]
        assert len(write_queries) == 1