"""dict-guard 统一风险路由测试（PR5：auto→R1/auto_applied、proposal→R2/proposal）。

mock 数据库/Neo4j/LLM/清理/影响面/告警/persist_record，dict_guard_daily
编排逻辑为真实产品代码（对齐 test_batch_extract_llm_outage 模式）。
"""

import asyncio
import unittest.mock as mock
from types import SimpleNamespace

from app.services.extraction.dict_guard import DictGuardDecision
from app.workers import dict_guard as worker_module
from app.workers.dict_guard import dict_guard_daily


class _FakeLLM:
    _providers = [{"name": "deepseek", "model": "deepseek-v4-flash"}]

    def __init__(self, decisions):
        self._queue = list(decisions)

    def call_with_fallback(self, prompt, response_model, **kwargs):
        return self._queue.pop(0)


class _FakeNeo4jSession:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeDriver:
    def session(self):
        return _FakeNeo4jSession()


class _FakeSessionF:
    def __init__(self):
        self.added: list = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def scalars(self, stmt):
        return SimpleNamespace(all=lambda: [])

    async def scalar(self, stmt):
        return None

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True


def _run(decisions, tmp_path=None, session=None):
    fake_llm = _FakeLLM(decisions)
    session = session or _FakeSessionF()
    persisted: list = []

    async def _fake_persist(record):
        persisted.append(record)
        return "rec"

    with (
        mock.patch.object(worker_module, "_fetch_suspect_rows", return_value=[
            {"name": "测试词A", "first_seen": "2026-08-24", "category": None, "req_count": 0},
            {"name": "测试词B", "first_seen": "2026-08-24", "category": None, "req_count": 0},
        ]),
        mock.patch.object(worker_module, "_load_recent_corpus", return_value=""),
        mock.patch.object(worker_module, "hard_gate", return_value=(True, "")),
        mock.patch.object(worker_module, "_fetch_suspect_positions", return_value=[]),
        mock.patch.object(worker_module, "_fetch_isolated_courses", return_value=[]),
        mock.patch.object(worker_module, "_load_semantic", return_value=None),
        mock.patch.object(worker_module, "_estimate_impact", return_value={
            "graph_nodes": 1, "jd_snapshots": 0,
        }),
        mock.patch.object(worker_module, "_apply_cleanup", return_value=1),
        mock.patch("app.core.database.async_session_factory", return_value=session),
        mock.patch("app.core.database.neo4j_driver", _FakeDriver()),
        mock.patch(
            "app.core.runtime_config.get",
            side_effect=lambda k, d=None: {
                "dict_guard_max_candidates": 20,
                "dict_guard_auto_impact_threshold": 50,
                "dict_guard_min_confidence": 0.8,
                "dict_guard_enabled": True,
                "dict_guard_reproposal_cooldown_days": 7,
            }.get(k, d),
        ),
        mock.patch(
            "app.services.extraction.llm_provider.LLMProviderChain", return_value=fake_llm,
        ),
        mock.patch("app.services.alerting.send_alert", return_value=None),
        mock.patch.object(worker_module, "_write_report", return_value=None),
        mock.patch("app.services.llm_decision.persist_record", side_effect=_fake_persist),
    ):
        summary = asyncio.run(dict_guard_daily({}))
    return summary, persisted, session


class TestUnifiedRiskRouting:
    def test_auto_applied_recorded_r1_auto_applied(self, tmp_path):
        decisions = [
            DictGuardDecision(action="add_stopword", term="测试词A", reason="噪音", confidence=0.95),
            DictGuardDecision(action="remove_stopword", term="测试词B", reason="真实技能", confidence=0.9),
        ]
        summary, persisted, session = _run(decisions, tmp_path=tmp_path)
        assert summary["auto_applied"] and summary["proposals"] == 1
        assert session.committed is True
        assert len(persisted) == 2
        by_term = {r.entity_id: r for r in persisted}
        auto = by_term["测试词A"]
        assert auto.domain == "governance"
        assert auto.status == "auto_applied"
        assert auto.risk_tier == "R1"
        assert auto.gate_result == "pass"
        assert auto.structured_output["action"] == "add_stopword"
        assert auto.provider == "deepseek"
        assert auto.model == "deepseek-v4-flash"
        proposal = by_term["测试词B"]
        assert proposal.status == "proposal"
        assert proposal.risk_tier == "R2"
        # 提案池同步建行（既有链路不破坏）
        assert any(getattr(obj, "action", "") == "remove_stopword" for obj in session.added)

    def test_record_persist_failure_only_counts(self, tmp_path):
        decisions = [
            DictGuardDecision(action="add_stopword", term="测试词A", reason="r", confidence=0.95),
        ]

        async def _boom(record):
            raise RuntimeError("pg down")

        session = _FakeSessionF()
        with (
            mock.patch.object(worker_module, "_fetch_suspect_rows", return_value=[
                {"name": "测试词A", "first_seen": "2026-08-24", "category": None, "req_count": 0},
            ]),
            mock.patch.object(worker_module, "_load_recent_corpus", return_value=""),
        mock.patch.object(worker_module, "hard_gate", return_value=(True, "")),
            mock.patch.object(worker_module, "_fetch_suspect_positions", return_value=[]),
            mock.patch.object(worker_module, "_fetch_isolated_courses", return_value=[]),
            mock.patch.object(worker_module, "_load_semantic", return_value=None),
            mock.patch.object(worker_module, "_estimate_impact", return_value={
                "graph_nodes": 1, "jd_snapshots": 0,
            }),
            mock.patch.object(worker_module, "_apply_cleanup", return_value=1),
            mock.patch("app.core.database.async_session_factory", return_value=session),
            mock.patch("app.core.database.neo4j_driver", _FakeDriver()),
            mock.patch(
            "app.core.runtime_config.get",
            side_effect=lambda k, d=None: {
                "dict_guard_max_candidates": 20,
                "dict_guard_auto_impact_threshold": 50,
                "dict_guard_min_confidence": 0.8,
                "dict_guard_enabled": True,
                "dict_guard_reproposal_cooldown_days": 7,
            }.get(k, d),
        ),
            mock.patch(
                "app.services.extraction.llm_provider.LLMProviderChain",
                return_value=_FakeLLM(decisions),
            ),
            mock.patch("app.services.alerting.send_alert", return_value=None),
            mock.patch.object(worker_module, "_write_report", return_value=None),
            mock.patch("app.services.llm_decision.persist_record", side_effect=_boom),
        ):
            summary = asyncio.run(dict_guard_daily({}))
        # 既有自动链路照常生效（动态过滤写 + DictChangeLog），仅决策记录失败计数
        assert summary["auto_applied"]
        assert summary["record_failed"] == 1
        assert session.committed is True

class TestRevertedSkip:
    def test_人工撤销后同实体动作跳过自动与提案(self, tmp_path):
        """治理救济通道：同实体+同动作曾有 reverted 记录 → 不自动生效、不重复提案。"""
        decisions = [
            DictGuardDecision(action="remove_node", term="脏课程", entity_type="course",
                              reason="孤立课程", confidence=0.9),
            DictGuardDecision(action="add_stopword", term="测试词B", reason="噪音",
                              confidence=0.9),
        ]

        class _RevertedSession(_FakeSessionF):
            async def scalar(self, stmt):
                return SimpleNamespace(id="rev-1", status="reverted")  # 任何前查均命中

        summary, persisted, session = _run(decisions, tmp_path=tmp_path,
                                           session=_RevertedSession())
        assert summary["auto_applied"] == []
        assert summary["proposals"] == 0
        assert persisted == []
        skipped_terms = {s["term"] for s in summary["skipped"]}
        assert {"脏课程", "测试词B"} <= skipped_terms
        assert all("撤销" in s["reason"] for s in summary["skipped"])
        assert not session.committed or session.added == []
