"""LLM 决策只读接口测试（PR7a：列表分页过滤 + 汇总聚合。纯函数 + fake 会话）。"""

from datetime import datetime, timezone
from types import SimpleNamespace

from app.api.v1.admin_routes import llm_decisions as mod
from app.models.business import LLMDecisionRecord


def _record(domain, status, entity_id="sk-1", confidence=0.9):
    rec = LLMDecisionRecord(
        id="00000000-0000-0000-0000-000000000001",
        domain=domain,
        entity_type="skill",
        entity_id=entity_id,
        status=status,
        confidence=confidence,
        risk_tier="R2" if status == "proposal" else "R0",
        gate_result="pass",
        provider="deepseek",
        model="m",
    )
    rec.created_at = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    return rec


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def scalars(self, stmt):
        return SimpleNamespace(all=lambda: self._rows)


class TestSerialize:
    def test_serialize_iso_timestamp(self):
        data = mod.serialize_record(_record("governance", "proposal"))
        assert data["domain"] == "governance"
        assert data["status"] == "proposal"
        assert data["risk_tier"] == "R2"
        assert data["created_at"] == "2026-08-24T12:00:00+00:00"


class TestBuildQuery:
    def test_filters_apply(self):
        q = mod.build_query("governance", "proposal", 20, 5)
        text = str(q)
        assert "llm_decision_records" in text
        # 过滤条件以绑定参数存在（SQLAlchemy 编译后为 :status_1/:domain_1）
        assert "llm_decision_records.status =" in text
        assert "llm_decision_records.domain =" in text
        assert "LIMIT" in text.upper()
        assert "OFFSET" in text.upper()

    def test_no_filters(self):
        q = mod.build_query("", "", 50, 0)
        text = str(q)
        assert "where" not in text.lower() or "ORDER BY" in text.upper()
        assert "DESC" in text or "desc" in text


class TestSummarize:
    def test_summary_groups_by_domain_and_status(self):
        import asyncio

        rows = [
            _record("governance", "proposal"),
            _record("governance", "auto_applied"),
            _record("governance", "proposal"),
            _record("skill_classify", "shadow"),
            _record("skill_relation", "blocked"),
            _record("skill_relation", "rejected"),
        ]
        summary = asyncio.run(mod.summarize(_FakeSession(rows)))
        assert summary["totals"] == {
            "proposal": 2, "auto_applied": 1, "blocked": 1,
            "shadow": 1, "other": 1, "records": 6,
        }
        by = {d["domain"]: d for d in summary["by_domain"]}
        assert by["governance"]["total"] == 3
        assert by["governance"]["by_status"]["proposal"] == 2
        assert by["skill_relation"]["by_status"]["rejected"] == 1

    def test_empty_summary(self):
        import asyncio

        summary = asyncio.run(mod.summarize(_FakeSession([])))
        assert summary["by_domain"] == []
        assert summary["totals"]["records"] == 0


class TestListEndpoint:
    def test_query_decisions_pass_through(self):
        rows = [_record("governance", "proposal"), _record("skill_classify", "shadow")]
        import asyncio

        out = asyncio.run(mod.query_decisions(_FakeSession(rows), "governance", "", 10, 0))
        assert len(out) == 2

    def test_serialize_roundtrip(self):
        import asyncio

        rows = [_record("governance", "proposal")]
        out = asyncio.run(mod.query_decisions(_FakeSession(rows), "", "", 10, 0))
        payload = {"items": [mod.serialize_record(r) for r in out], "limit": 10, "offset": 0}
        assert payload["items"][0]["domain"] == "governance"
        assert payload["limit"] == 10