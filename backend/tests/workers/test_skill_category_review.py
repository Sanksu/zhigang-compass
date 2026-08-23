"""技能分类 LLM 审查测试（服务层 schema/触发门/降级 + worker 编排 + 注册契约）。"""

import asyncio
import json
import unittest.mock as mock

import pytest
from pydantic import ValidationError

from app.services.extraction.skill_category_review import (
    SkillCategorySuggestion,
    classify_skill,
    should_classify,
)
from app.workers.skill_category_review import skill_category_review_daily


class TestSchemaEnumConstraint:
    def test_known_category_accepted(self):
        s = SkillCategorySuggestion(category="前端", confidence=0.9, reason="r")
        assert s.category == "前端"

    def test_unknown_category_rejected(self):
        with pytest.raises(ValidationError):
            SkillCategorySuggestion(category="不存在的分类XYZ")

    def test_sentinel_uncategorized_rejected(self):
        with pytest.raises(ValidationError):
            SkillCategorySuggestion(category="未分类")

    def test_confidence_bounds(self):
        with pytest.raises(ValidationError):
            SkillCategorySuggestion(category="前端", confidence=1.5)


class TestShouldClassify:
    def test_low_ref_unclassified_true(self):
        assert should_classify("某新技能X9", req_count=0, has_suggestion=False) is True
        assert should_classify("某新技能X9", req_count=3, has_suggestion=False) is True

    def test_high_ref_false(self):
        assert should_classify("某新技能X9", req_count=4, has_suggestion=False) is False

    def test_existing_suggestion_false(self):
        assert should_classify("某新技能X9", req_count=0, has_suggestion=True) is False

    def test_short_name_false(self):
        assert should_classify("A", req_count=0, has_suggestion=False) is False


class _FakeLLM:
    def __init__(self, outcome=None, error=None):
        self._outcome = outcome
        self._error = error

    def extract_structured(self, prompt, response_model, system_prompt=None, timeout=None):
        if self._error is not None:
            raise self._error
        return self._outcome


class TestClassifySkill:
    def test_llm_none_degrades(self):
        assert classify_skill("X", None) is None

    def test_llm_error_degrades_to_none(self):
        from app.services.extraction.llm_provider import LLMExtractionError

        llm = _FakeLLM(error=LLMExtractionError("超时"))
        assert classify_skill("某技能", llm) is None

    def test_success_returns_suggestion_with_15s_timeout(self):
        outcome = SkillCategorySuggestion(category="前端")
        _FakeLLM(outcome=outcome)

        class _Capture(_FakeLLM):
            def extract_structured(self, prompt, response_model, system_prompt=None, timeout=None):
                self.timeout = timeout
                self.prompt = prompt
                return super().extract_structured(prompt, response_model, system_prompt, timeout)

        cap = _Capture(outcome=outcome)
        assert classify_skill("React", cap).category == "前端"
        assert cap.timeout == 15
        assert '"React"' in cap.prompt


# ---- worker 编排 ----


class _FakeNeo4jSession:
    def __init__(self):
        self.queries: list[tuple] = []

    def run(self, query, **params):
        self.queries.append((query, params))
        return []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeDriver:
    def __init__(self):
        self.session_obj = _FakeNeo4jSession()

    def session(self):
        return self.session_obj


def _run(rows, llm_outcome=None, enabled=True, monkeypatch=None, tmp_path=None):
    from app.workers import skill_category_review as worker_module

    driver = _FakeDriver()

    class _Chain:
        pass

    outcome = llm_outcome or SkillCategorySuggestion(category="前端", confidence=0.8)
    fake_llm = _FakeLLM(outcome=outcome)

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
        mock.patch(
            "app.services.extraction.llm_provider.LLMProviderChain",
            return_value=fake_llm,
        ),
        mock.patch.object(worker_module, "_REPORT_DIR", tmp_path),
    ):
        summary = asyncio.run(skill_category_review_daily({}))
    return summary, driver, fake_llm, None



def _today():
    from datetime import datetime, timedelta, timezone

    cst = timezone(timedelta(hours=8))
    return datetime.now(cst).strftime("%Y-%m-%d")


class TestWorkerOrchestration:
    def test_disabled_by_default_skips(self, tmp_path, monkeypatch):
        summary, driver, *_ = _run([], enabled=False, monkeypatch=monkeypatch, tmp_path=tmp_path)
        assert summary["status"] == "skipped"

    def test_unclassified_skill_gets_suggestion_written(self, tmp_path, monkeypatch):
        rows = [{"name": "某新技能X9", "req_count": 0, "suggested_category": None}]
        summary, driver, _, _ = _run(rows, monkeypatch=monkeypatch, tmp_path=tmp_path)

        assert summary["status"] == "ok"
        assert len(summary["classified"]) == 1
        assert summary["classified"][0]["category"] == "前端"
        # 写入的是 suggested_* 提议字段，权威 category 不出现在 SET 子句
        write_query, write_params = [q for q in driver.session_obj.queries if "SET" in q[0]][0]
        assert "s.suggested_category" in write_query
        assert "s.category =" not in write_query
        assert write_params["name"] == "某新技能X9"

    def test_existing_suggestion_not_reclassified(self, tmp_path, monkeypatch):
        rows = [{"name": "已有提议技能", "req_count": 0, "suggested_category": "前端"}]
        summary, driver, _, _ = _run(rows, monkeypatch=monkeypatch, tmp_path=tmp_path)
        assert summary["candidates"] == 0
        assert summary["classified"] == []

    def test_report_written(self, tmp_path, monkeypatch):
        rows = [{"name": "某新技能X9", "req_count": 0, "suggested_category": None}]
        summary, _, _, _ = _run(rows, monkeypatch=monkeypatch, tmp_path=tmp_path)
        report = json.loads(
            (tmp_path / f"skill_category_review_{_today()}.json").read_text(encoding="utf-8")
        )
        assert report["classified"][0]["name"] == "某新技能X9"


class TestRegistrationContract:
    def test_registered_in_worker_settings(self):
        from app.workers.settings import WorkerSettings
        from app.workers.tasks import skill_category_review_daily as facade_task

        assert skill_category_review_daily in WorkerSettings.functions
        assert facade_task is not None

    def test_facade_same_source_and_etl_reference(self):
        import inspect

        import app.workers.etl as etl_module
        from app.workers import tasks as facade
        from app.workers.skill_category_review import (
            skill_category_review_daily as impl,
        )

        assert facade.skill_category_review_daily is impl
        source = inspect.getsource(etl_module)
        assert "tasks_module.skill_category_review_daily(ctx)" in source
        assert '"skill_category_review"' in source
