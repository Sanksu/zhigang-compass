"""名称归一审批执行通道测试（PR3 c：position/skill normalize approve/reject 端点协程）。

asyncio.run 直调（注入 fake async session 与 current_user，项目纯函数风格），
对齐 test_skill_relation_approval.py 的写法。
"""

import asyncio
from types import SimpleNamespace

from app.api.v1.admin_routes import llm_decisions as mod

_OPERATOR = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _skill_record(status="proposal", action="merge", target="Java", source="Javascript", entity_id=None):
    """skill_normalize 决策记录。entity_id 为原始技能名（unormalized）。"""
    return SimpleNamespace(
        id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        domain="skill_normalize",
        status=status,
        entity_id=entity_id or source,
        entity_type="skill",
        structured_output={"action": action, "target_standard": target,
                           "confidence": 0.9, "reason": "variant"},
        reviewer="", review_reason="", effects_applied=False,
    )


def _position_record(status="proposal", canonical="后端开发工程师", source="后端工程师",
                     is_new=False, keep_original=False, entity_id=None):
    """position_normalize 决策记录。entity_id 为归一化岗位名（Position 节点名）。"""
    return SimpleNamespace(
        id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        domain="position_normalize",
        status=status,
        entity_id=entity_id or source,
        entity_type="position",
        structured_output={"canonical_name": canonical, "is_new": is_new,
                           "keep_original": keep_original, "confidence": 0.9, "reason": "alias"},
        reviewer="", review_reason="", effects_applied=False,
    )


class _FakeSession:
    def __init__(self, record, existing_requests=()):
        self._record = record
        self._existing_requests = list(existing_requests)
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
        rows = self._existing_requests
        return SimpleNamespace(
            all=lambda: rows, first=lambda: rows[0] if rows else None,
        )

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


def _approve(session, reason="归并确认", operator=_OPERATOR):
    return asyncio.run(mod.approve_llm_decision(
        session._record.id, {"review_reason": reason},
        db=session, current_user={"sub": operator, "role": "admin"},
    ))


def _code(resp) -> int:
    """统一取业务码：ok() 返回 APIResponse（.code 属性），error() 返回 JSONResponse。"""
    import json as _json

    if hasattr(resp, "model_dump"):
        return resp.code
    return int(_json.loads(resp.body)["code"])


class TestSkillNormalizeApprove:
    def test_merge_writes_request_and_audit(self):
        session = _FakeSession(_skill_record())
        resp = _approve(session)
        assert _code(resp) == 0
        assert session.committed is True
        assert session._record.status == "approved"
        assert session._record.effects_applied is True
        kinds = {type(obj).__name__ for obj in session.added}
        assert {"NameNormalizationRequest", "AuditLog"} <= kinds
        req = [o for o in session.added if type(o).__name__ == "NameNormalizationRequest"][0]
        assert (req.entity_type, req.action, req.source_name, req.target_name) == \
            ("skill", "merge", "Javascript", "Java")
        assert req.proposal_id == session._record.id
        assert req.reviewed_by == _OPERATOR

    def test_keep_action_is_noop(self):
        """keep 动作视为确认原样，无图变更（仅决策置 approved）。"""
        session = _FakeSession(_skill_record(action="keep", target=""))
        resp = _approve(session)
        assert _code(resp) == 0
        assert session._record.status == "approved"
        assert not any(type(o).__name__ == "NameNormalizationRequest" for o in session.added)

    def test_duplicate_proposal_conflict(self):
        from app.models.business import NameNormalizationRequest as _NNR

        existing = _NNR(proposal_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                        entity_type="skill", action="merge",
                        source_name="Javascript", target_name="Java")
        session = _FakeSession(_skill_record(), existing_requests=[existing])
        resp = _approve(session)
        assert _code(resp) != 0
        assert session.committed is False
        assert not any(type(o).__name__ == "NameNormalizationRequest" for o in session.added)


class TestPositionNormalizeApprove:
    def test_merge_writes_request(self):
        session = _FakeSession(_position_record(canonical="后端开发工程师", source="后端工程师"))
        resp = _approve(session)
        assert _code(resp) == 0
        req = [o for o in session.added if type(o).__name__ == "NameNormalizationRequest"][0]
        assert (req.entity_type, req.action, req.source_name, req.target_name) == \
            ("position", "merge", "后端工程师", "后端开发工程师")

    def test_is_new_becomes_rename(self):
        session = _FakeSession(_position_record(canonical="量子计算工程师", source="量子工程师", is_new=True))
        resp = _approve(session)
        assert _code(resp) == 0
        req = [o for o in session.added if type(o).__name__ == "NameNormalizationRequest"][0]
        assert req.action == "rename"

    def test_keep_original_is_noop(self):
        session = _FakeSession(_position_record(canonical="后端工程师", source="后端工程师", keep_original=True))
        resp = _approve(session)
        assert _code(resp) == 0
        assert not any(type(o).__name__ == "NameNormalizationRequest" for o in session.added)


class TestApproveCommonGuard:
    def test_wrong_domain_rejected(self):
        rec = _skill_record()
        rec.domain = "governance"
        session = _FakeSession(rec)
        resp = _approve(session)
        assert _code(resp) != 0
        assert session.committed is False

    def test_conflict_on_non_proposal(self):
        session = _FakeSession(_skill_record(status="shadow"))
        resp = _approve(session)
        assert _code(resp) != 0

    def test_requires_reason(self):
        session = _FakeSession(_skill_record())
        resp = _approve(session, reason="")
        assert _code(resp) != 0

    def test_invalid_operator_uuid_rejected(self):
        session = _FakeSession(_skill_record())
        resp = _approve(session, operator="not-a-uuid")
        assert _code(resp) != 0
        assert session.committed is False


class TestRejectEndpoint:
    def test_reject_any_mutable_domain(self):
        """reject 对名称归一同样只做状态流转（效果为 0）。"""
        session = _FakeSession(_skill_record())
        resp = asyncio.run(mod.reject_llm_decision(
            session._record.id, {"review_reason": "关系不成立"},
            db=session, current_user={"sub": _OPERATOR, "role": "admin"},
        ))
        assert _code(resp) == 0
        assert session._record.status == "rejected"
        assert not any(type(o).__name__ == "NameNormalizationRequest" for o in session.added)
