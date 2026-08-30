"""LLM 决策只读接口测试（PR7a：列表分页过滤 + 汇总聚合。纯函数 + fake 会话）。"""

import json
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


# ---- 治理救济通道：auto_applied 撤销（2026-08-31 方案 A） ----


def _auto_record(entity_type="course", entity_id="某课程", action="remove_node",
                 created_at=None, domain="governance", status="auto_applied"):
    rec = LLMDecisionRecord(
        id="00000000-0000-0000-0000-000000000009",
        domain=domain,
        entity_type=entity_type,
        entity_id=entity_id,
        status=status,
        confidence=0.85,
        risk_tier="R1",
        gate_result="pass",
        provider="commandcode",
        model="m",
        structured_output={"action": action, "reason": "孤立课程"},
    )
    rec.created_at = created_at or datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    return rec


class _UndoFakeSession(_FakeSession):
    """undo 端点桩：session.get 返回记录；add/commit 记账。"""

    def __init__(self, record):
        self.record = record
        self.added = []
        self.committed = False

    async def get(self, model, key):
        return self.record

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True


_ADMIN = {"sub": "00000000-0000-0000-0000-0000000000aa", "role": "admin"}


def _undo(db, record=None):
    import asyncio

    from app.schemas.admin_requests import LLMDecisionReviewRequest

    rec = record if record is not None else db.record
    decision_id = str(rec.id) if rec is not None else "00000000-0000-0000-0000-000000000009"
    return asyncio.run(mod.undo_llm_decision(
        decision_id=decision_id,
        req=LLMDecisionReviewRequest(review_reason="误删救济"),
        db=db,
        current_user=_ADMIN,
    ))


def _resp_code(resp):
    import json

    return json.loads(resp.body)["code"]


class TestUndoGuards:
    def test_非governance域不可撤销(self):
        rec = _auto_record(domain="skill_normalize", status="proposal")
        resp = _undo(_UndoFakeSession(rec), rec)
        assert _resp_code(resp) == 4090

    def test_非auto_applied状态不可撤销(self):
        rec = _auto_record(status="proposal")
        resp = _undo(_UndoFakeSession(rec), rec)
        assert _resp_code(resp) == 4090

    def test_不可撤销动作409(self):
        rec = _auto_record(action="hide_node")
        resp = _undo(_UndoFakeSession(rec), rec)
        assert _resp_code(resp) == 4090

    def test_position节点删除409(self):
        rec = _auto_record(entity_type="position", entity_id="脏岗位")
        resp = _undo(_UndoFakeSession(rec), rec)
        assert _resp_code(resp) == 4090

    def test_超窗409(self):
        old = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        rec = _auto_record(created_at=old)
        resp = _undo(_UndoFakeSession(rec), rec)
        assert _resp_code(resp) == 4090

    def test_记录不存在404(self):
        db = _UndoFakeSession(None)
        resp = _undo(db)
        assert _resp_code(resp) == 4040

    def test_操作者非UUID不落库(self, monkeypatch):
        import asyncio

        from app.schemas.admin_requests import LLMDecisionReviewRequest

        rec = _auto_record()
        db = _UndoFakeSession(rec)
        resp = asyncio.run(mod.undo_llm_decision(
            decision_id=str(rec.id),
            req=LLMDecisionReviewRequest(review_reason="x"),
            db=db,
            current_user={"sub": "not-a-uuid", "role": "admin"},
        ))
        assert _resp_code(resp) == 4000
        assert not db.committed


class TestUndoEffects:
    def test_移除停用词并置reverted写审计(self, monkeypatch):
        calls = {}

        def fake_remove(kind, term):
            calls["args"] = (kind, term)
            return True

        monkeypatch.setattr(
            "app.services.extraction.dynamic_filters.remove_entry", fake_remove)
        rec = _auto_record(entity_type="skill", entity_id="Asana", action="add_stopword")
        db = _UndoFakeSession(rec)
        resp = _undo(db)

        assert calls["args"] == ("blocked", "Asana")
        assert rec.status == "reverted"
        assert rec.reviewer == _ADMIN["sub"]
        assert rec.rollback_ref == "filter_removed:True"
        assert db.committed and len(db.added) == 1
        assert db.added[0].action == "llm_decision_undo"
        data = json.loads(resp.body)["data"] if hasattr(resp, "body") else resp.data
        assert data["filter_removed"] is True

    def test_课程重建并回写rebuilt(self, monkeypatch):
        async def fake_rebuild(term):
            assert term == "某课程"
            return 1

        monkeypatch.setattr(mod, "_rebuild_course_nodes", fake_rebuild)
        rec = _auto_record(entity_type="course", entity_id="某课程", action="remove_node")
        db = _UndoFakeSession(rec)
        resp = _undo(db)

        assert rec.status == "reverted"
        assert rec.rollback_ref == "course_rebuilt:1"
        data = json.loads(resp.body)["data"] if hasattr(resp, "body") else resp.data
        assert data["rebuilt"] == 1 and data["notes"] == []

    def test_原始缺失走notes部分撤销(self, monkeypatch):
        async def fake_rebuild(term):
            return 0

        monkeypatch.setattr(mod, "_rebuild_course_nodes", fake_rebuild)
        rec = _auto_record()
        db = _UndoFakeSession(rec)
        resp = _undo(db)

        assert rec.status == "reverted"
        assert rec.rollback_ref == "course_rebuilt:0"
        data = json.loads(resp.body)["data"] if hasattr(resp, "body") else resp.data
        assert data["rebuilt"] == 0 and data["notes"]

    def test_课程脏边撤销按课程名重建(self, monkeypatch):
        seen = {}

        async def fake_rebuild(term):
            seen["term"] = term
            return 2

        monkeypatch.setattr(mod, "_rebuild_course_nodes", fake_rebuild)
        rec = _auto_record(action="remove_edge", entity_id="Python→某课程")
        db = _UndoFakeSession(rec)
        _undo(db)
        assert seen["term"] == "某课程"
        assert rec.rollback_ref == "course_rebuilt:2"