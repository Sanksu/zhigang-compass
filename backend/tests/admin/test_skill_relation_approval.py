"""技能关系审批执行通道测试（PR9b1：approve/reject 端点协程）。

asyncio.run 直调（注入 fake async session 与 current_user，项目纯函数风格）。
"""

import asyncio

from app.schemas.admin_requests import LLMDecisionReviewRequest
from types import SimpleNamespace

from app.api.v1.admin_routes import llm_decisions as mod

_OPERATOR = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _rel_record(status="proposal", entity_id="Java->Spring", relation="PREREQUISITE_OF"):
    return SimpleNamespace(
        id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        domain="skill_relation",
        status=status,
        entity_id=entity_id,
        structured_output={"relation": relation, "direction": "a_to_b"},
        reviewer="", review_reason="", effects_applied=False,
    )


class _FakeSession:
    def __init__(self, record, existing_relations=()):
        self._record = record
        self._existing_relations = list(existing_relations)
        self.added: list = []
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, model, decision_id):
        return self._record

    async def scalars(self, stmt):
        rows = self._existing_relations
        return SimpleNamespace(
            all=lambda: rows, first=lambda: rows[0] if rows else None,
        )

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


def _approve(session, reason="先修语义确认", operator=_OPERATOR):
    return asyncio.run(mod.approve_llm_decision(
        session._record.id, LLMDecisionReviewRequest(review_reason=reason),
        db=session, current_user={"sub": operator, "role": "admin"},
    ))


def _code(resp) -> int:
    """统一取业务码：ok() 返回 APIResponse（.code 属性），error() 返回 JSONResponse。"""
    import json as _json

    if hasattr(resp, "model_dump"):
        return resp.code
    return int(_json.loads(resp.body)["code"])



class TestApproveEndpoint:
    def test_approve_writes_dynamic_relation_and_audit(self):
        session = _FakeSession(_rel_record())
        resp = _approve(session)
        assert _code(resp) == 0
        assert session.committed is True
        assert session._record.status == "approved"
        assert session._record.effects_applied is True
        kinds = {type(obj).__name__ for obj in session.added}
        assert {"SkillDynamicRelation", "AuditLog"} <= kinds
        dyn = [o for o in session.added if type(o).__name__ == "SkillDynamicRelation"][0]
        assert (dyn.source_skill, dyn.target_skill, dyn.relation_type) == ("Java", "Spring", "PREREQUISITE_OF")
        assert dyn.proposal_id == session._record.id
        assert dyn.reviewed_by == _OPERATOR

    def test_approve_rejects_non_relation_domain(self):
        rec = _rel_record()
        rec.domain = "governance"
        session = _FakeSession(rec)
        resp = _approve(session)
        assert _code(resp) != 0
        assert session.committed is False  # 校验失败不落库

    def test_approve_conflict_on_non_proposal(self):
        session = _FakeSession(_rel_record(status="shadow"))
        resp = _approve(session)
        assert _code(resp) != 0

    def test_approve_requires_reason(self):
        """空 review_reason 由 Pydantic 强校验拦截（HTTP 侧 422/4000）。"""
        import pytest
        from pydantic import ValidationError

        from app.schemas.admin_requests import LLMDecisionReviewRequest

        with pytest.raises(ValidationError):
            LLMDecisionReviewRequest(review_reason="")

    def test_approve_rejects_malformed_entity_id(self):
        session = _FakeSession(_rel_record(entity_id="Java"))
        resp = _approve(session)
        assert _code(resp) != 0

    def test_approve_duplicate_relation_conflict(self):
        """同对关系已批准过（脚本重跑产生重复 proposal）→ ERR_CONFLICT 不落库。"""
        from app.models.business import SkillDynamicRelation as _SDR

        existing = _SDR(
            source_skill="Java", target_skill="Spring",
            relation_type="PREREQUISITE_OF", proposal_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
        )
        session = _FakeSession(_rel_record(), existing_relations=[existing])
        resp = _approve(session)
        assert _code(resp) != 0
        import json as _json

        assert "已批准过" in _json.loads(resp.body)["msg"]
        assert session.committed is False
        assert not any(type(o).__name__ == "SkillDynamicRelation" for o in session.added)

    def test_approve_integrity_error_rolls_back(self):
        """并发兜底：commit 撞唯一约束 → 回滚 + 冲突响应（不 500）。"""
        import json as _json

        from starlette.responses import JSONResponse

        class _IntegritySession(_FakeSession):
            async def commit(self):
                from sqlalchemy.exc import IntegrityError

                raise IntegrityError("dup", None, Exception())

        session = _IntegritySession(_rel_record())
        resp = _approve(session)
        assert isinstance(resp, JSONResponse)
        assert _json.loads(resp.body)["code"] != 0
        assert session.rolled_back is True
        assert "并发批准冲突" in _json.loads(resp.body)["msg"]

    def test_reject_only_status(self):
        session = _FakeSession(_rel_record())
        resp = asyncio.run(mod.reject_llm_decision(
            session._record.id, LLMDecisionReviewRequest(review_reason="关系不成立"),
            db=session, current_user={"sub": _OPERATOR, "role": "admin"},
        ))
        assert _code(resp) == 0
        assert session.committed is True
        assert session._record.status == "rejected"
        assert session._record.review_reason == "关系不成立"
        assert not any(type(o).__name__ == "SkillDynamicRelation" for o in session.added)
