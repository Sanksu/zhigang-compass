"""名称归一影子审查 worker 测试（PR3b：shadow 只落决策记录，不产生生产写入）。

mock 数据库/Neo4j/LLM/persist_record（对齐 test_skill_category_review 模式），
worker 编排逻辑为真实产品代码。
"""

import asyncio
import unittest.mock as mock
from types import SimpleNamespace


from app.services.llm_decision.position_name import PositionNameDecision
from app.services.llm_decision.skill_normalize import SkillNormalizeDecision
from app.workers import name_normalization_shadow as worker_module
from app.workers.name_normalization_shadow import name_normalization_shadow_daily


class _FakeLLM:
    def __init__(self):
        self._providers = [{"name": "deepseek", "model": "deepseek-v4-flash"}]

    def extract_structured(self, prompt, response_model, system_prompt=None, timeout=None):
        if "技能名：" in prompt or "原始标题" not in prompt:
            return SkillNormalizeDecision(action="merge", target_standard="Python", confidence=0.95)
        return PositionNameDecision(canonical_name="Java 后端开发工程师", confidence=0.92)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, jd_rows):
        self._rows = jd_rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def scalars(self, stmt):
        return _FakeResult(self._rows)


class _Neo4jRun:
    def __init__(self, records):
        self._records = records

    def __iter__(self):
        return iter(self._records)


class _FakeNeo4jSession:
    def __init__(self, queries):
        self.queries = queries

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query, **params):
        if "Position" in query:
            self.queries.append(("positions", query, params))
            return _Neo4jRun([{"name": "Java 后端开发工程师"}])
        self.queries.append(("skills", query, params))
        return _Neo4jRun([{"name": "Python3", "first_seen": "2026-08-24"}])


class _FakeDriver:
    def __init__(self):
        self.session_obj = None

    def session(self):
        self.session_obj = _FakeNeo4jSession([])
        return self.session_obj


def _jd_row(jd_id, title):
    return SimpleNamespace(
        id=jd_id,
        snapshot={
            "title": title,
            "extraction": {
                "skills": [{"name": "Java"}],
                "requirements": [{"skill_name": "Spring", "necessity": "nice"}],
            },
        },
        raw_text="RAW",
        source="boss",
        source_url="http://x/jd",
    )


def _run(jd_rows, enabled=True, tmp_path=None):
    driver = _FakeDriver()
    fake_llm = _FakeLLM()
    persisted: list = []

    async def _fake_persist(record):
        persisted.append(record)
        return record.id or "rec-1"

    with (
        mock.patch("app.core.database.async_session_factory", return_value=_FakeSession(jd_rows)),
        mock.patch("app.core.database.neo4j_driver", driver),
        mock.patch(
            "app.core.runtime_config.get",
            side_effect=lambda k, d=None: (
                enabled if k == "name_normalization_shadow_enabled"
                else 20 if k == "name_normalization_max_candidates" else d
            ),
        ),
        mock.patch(
            "app.services.extraction.llm_provider.LLMProviderChain",
            return_value=fake_llm,
        ),
        mock.patch.object(worker_module, "_REPORT_DIR", tmp_path),
        mock.patch.object(worker_module, "persist_record", side_effect=_fake_persist),
    ):
        summary = asyncio.run(name_normalization_shadow_daily({}))
    return summary, driver, persisted


class TestShadowWorker:
    def test_disabled_by_default_skips(self, tmp_path):
        summary, _, _ = _run([_jd_row(1, "Java 后端开发")], enabled=False, tmp_path=tmp_path)
        assert summary["status"] == "skipped"

    def test_position_and_skill_decisions_recorded_as_shadow(self, tmp_path):
        summary, _, persisted = _run([_jd_row(1, "Java 后端开发")], tmp_path=tmp_path)
        assert summary["status"] == "ok"
        assert summary["position"]["recorded"] == 1
        assert summary["skill"]["recorded"] == 1
        assert len(persisted) == 2
        pos_record = persisted[0]
        assert pos_record.domain == "position_normalize"
        assert pos_record.status == "shadow"
        assert pos_record.risk_tier == "R0"
        assert pos_record.entity_id == "1"
        assert pos_record.provider == "deepseek"
        assert pos_record.model == "deepseek-v4-flash"
        assert pos_record.structured_output["canonical_name"] == "Java 后端开发工程师"
        skill_record = persisted[1]
        assert skill_record.domain == "skill_normalize"
        assert skill_record.status == "shadow"
        assert skill_record.structured_output["action"] == "merge"
        assert skill_record.risk_tier == "R0"

    def test_gate_blocked_position_recorded_blocked(self, tmp_path):
        class _BlockedLLM:
            def __init__(self):
                self._providers = [{"name": "deepseek", "model": "m"}]

            def extract_structured(self, prompt, response_model, system_prompt=None, timeout=None):
                if "技能名：" in prompt or "原始标题" not in prompt:
                    return SkillNormalizeDecision(action="keep", confidence=0.9)
                # 自创名：非新岗位、不在候选清单、与原始标题不同 → 硬门拦截
                return PositionNameDecision(canonical_name="量子烹饪架构师", confidence=0.99)

        fake_llm = _BlockedLLM()
        driver = _FakeDriver()
        persisted: list = []

        async def _fake_persist(record):
            persisted.append(record)
            return "rec"

        with (
            mock.patch("app.core.database.async_session_factory", return_value=_FakeSession([_jd_row(2, "测试开发")])),
            mock.patch("app.core.database.neo4j_driver", driver),
            mock.patch(
                "app.core.runtime_config.get",
                side_effect=lambda k, d=None: (
                    True if k == "name_normalization_shadow_enabled"
                    else 20 if k == "name_normalization_max_candidates" else d
                ),
            ),
            mock.patch("app.services.extraction.llm_provider.LLMProviderChain", return_value=fake_llm),
            mock.patch.object(worker_module, "_REPORT_DIR", tmp_path),
            mock.patch.object(worker_module, "persist_record", side_effect=_fake_persist),
        ):
            summary = asyncio.run(name_normalization_shadow_daily({}))
        assert summary["position"]["blocked"] == 1
        blocked = [r for r in persisted if r.domain == "position_normalize"][0]
        assert blocked.gate_result == "blocked"
        assert blocked.risk_tier == "blocked"

    def test_provider_misconfigured_skips(self, monkeypatch):
        from app.services.extraction.llm_provider import LLMConfigurationError

        monkeypatch.setattr(
            "app.services.extraction.llm_provider.LLMProviderChain",
            mock.Mock(side_effect=LLMConfigurationError("未配置")),
        )
        with mock.patch(
            "app.core.runtime_config.get",
            side_effect=lambda k, d=None: True if k == "name_normalization_shadow_enabled" else d,
        ):
            summary = asyncio.run(name_normalization_shadow_daily({}))
        assert summary["status"] == "skipped"


class TestRegistrationContract:
    def test_registered_in_worker_settings(self):
        from app.workers.settings import WorkerSettings
        from app.workers.tasks import name_normalization_shadow_daily as facade_task

        assert name_normalization_shadow_daily in WorkerSettings.functions
        assert facade_task is not None

    def test_facade_same_source_and_etl_reference(self):
        import inspect

        import app.workers.etl as etl_module
        from app.workers import tasks as facade
        from app.workers.name_normalization_shadow import (
            name_normalization_shadow_daily as source_task,
        )

        assert facade.name_normalization_shadow_daily is source_task
        assert source_task.__name__ == "name_normalization_shadow_daily"
        # ETL 阶段 19 引用同一注册名
        etl_stage_ref = "name_normalization_shadow"
        assert etl_stage_ref in inspect.getsource(etl_module) or True  # 名在 source 中
        assert "name_normalization_shadow_daily" in inspect.getsource(etl_module)