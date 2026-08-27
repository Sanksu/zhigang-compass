"""技能分类审批执行通道测试（PR 补：skill_classify shadow → approved 端点协程）。

asyncio.run 直调（注入 fake async session 与 current_user），对齐
test_name_normalization_approval.py 的写法。skill_classify 记录为 shadow
（worker 验收语义），approve/reject 接受 shadow 状态。
"""

import asyncio

from app.schemas.admin_requests import LLMDecisionReviewRequest
from types import SimpleNamespace

from app.api.v1.admin_routes import llm_decisions as mod

_OPERATOR = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _classify_record(status="shadow", category="云计算", skill="Kubernetes"):
    return SimpleNamespace(
        id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        domain="skill_classify",
        status=status,
        entity_id=skill,
        entity_type="skill",
        structured_output={"category": category, "reason": "云平台技术"},
        reviewer="", review_reason="", effects_applied=False,
    )


class _FakeSession:
    def __init__(self, record, existing_approvals=()):
        self._record = record
        self._existing_approvals = list(existing_approvals)
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
        rows = self._existing_approvals
        return SimpleNamespace(
            all=lambda: rows, first=lambda: rows[0] if rows else None,
        )

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


def _approve(session, reason="分类确认", operator=_OPERATOR):
    return asyncio.run(mod.approve_llm_decision(
        session._record.id, LLMDecisionReviewRequest(review_reason=reason),
        db=session, current_user={"sub": operator, "role": "admin"},
    ))


def _code(resp) -> int:
    import json as _json

    if hasattr(resp, "model_dump"):
        return resp.code
    return int(_json.loads(resp.body)["code"])


class TestSkillClassifyApprove:
    def test_shadow_approve_writes_approval(self):
        """skill_classify shadow 可批准（worker 验收语义）；落 SkillCategoryApproval。"""
        session = _FakeSession(_classify_record())
        resp = _approve(session)
        assert _code(resp) == 0
        assert session.committed is True
        assert session._record.status == "approved"
        # 图写由 sync_* 脚本执行——approve 置 False 待落图（#570 对账语义）
        assert session._record.effects_applied is False
        kinds = {type(obj).__name__ for obj in session.added}
        assert {"SkillCategoryApproval", "AuditLog"} <= kinds
        req = [o for o in session.added if type(o).__name__ == "SkillCategoryApproval"][0]
        assert (req.skill_name, req.category) == ("Kubernetes", "云计算")
        assert req.proposal_id == session._record.id
        assert req.reviewed_by == _OPERATOR

    def test_duplicate_proposal_conflict(self):
        from app.models.business import SkillCategoryApproval as _SCA

        existing = _SCA(proposal_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                        skill_name="Kubernetes", category="云计算")
        session = _FakeSession(_classify_record(), existing_approvals=[existing])
        resp = _approve(session)
        assert _code(resp) != 0
        assert session.committed is False
        assert not any(type(o).__name__ == "SkillCategoryApproval" for o in session.added)

    def test_missing_category_rejected(self):
        session = _FakeSession(_classify_record(category=""))
        resp = _approve(session)
        assert _code(resp) != 0
        assert session.committed is False


class TestSkillClassifyReject:
    def test_shadow_reject_only_flow(self):
        """reject 对 skill_classify shadow 仅状态流转（效果为 0）。"""
        session = _FakeSession(_classify_record())
        resp = asyncio.run(mod.reject_llm_decision(
            session._record.id, LLMDecisionReviewRequest(review_reason="分类不当"),
            db=session, current_user={"sub": _OPERATOR, "role": "admin"},
        ))
        assert _code(resp) == 0
        assert session._record.status == "rejected"
        assert not any(type(o).__name__ == "SkillCategoryApproval" for o in session.added)


class TestGuardRegression:
    def test_skill_relation_proposal_still_works(self):
        """非 skill_classify 域不变：关系域仍要求 proposal 状态。"""
        rec = SimpleNamespace(
            id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            domain="skill_relation", status="shadow",
            entity_id="Java->Python", entity_type="skill_relation",
            structured_output={"relation": "ALTERNATIVE_OF", "direction": "a_to_b"},
            reviewer="", review_reason="", effects_applied=False,
        )
        session = _FakeSession(rec)
        resp = _approve(session)
        assert _code(resp) != 0  # 关系域 shadow 不可直接批准（仍须 proposal）

    def test_wrong_domain_rejected(self):
        rec = _classify_record()
        rec.domain = "governance"
        session = _FakeSession(rec)
        resp = _approve(session)
        assert _code(resp) != 0
        assert session.committed is False