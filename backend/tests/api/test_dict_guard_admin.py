"""dict-guard 管理端路由测试：审核执行语义 / 回滚防复滚 / 报告读取。

直调 handler（FastAPI 装饰器返回原函数）+ FakeDB + dynamic_filters 间谍，
覆盖方案 §5 风险不对称语义在审批侧的落地：
- approve add_stopword → 动态 blocked + scoped 清理 + changelog(manual/blocked)
- approve remove_stopword → 动态条目移除；静态词以受影响技能 protect 落地
- approve protect_whitelist → 动态 protected
- rollback 反向操作 + 防复滚；report/latest 取最新
"""

import json
import types

import pytest

import app.api.v1.admin_routes.dict_guard as dg


# ── 桩件 ──────────────────────────────────────────────────────────

def _proposal(**kw):
    base = dict(
        id="p1", term="低代码平台搭建", action="add_stopword", status="pending",
        reason="噪音词条", llm_confidence=0.6, evidence=[],
        impact_stats={"graph_nodes": 3, "jd_snapshots": 5}, run_date="2026-08-21",
        reviewed_by="", review_reason="", reviewed_at=None, created_at=None,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


def _changelog(**kw):
    base = dict(
        id="c1", term="低代码平台搭建", action="add_stopword", source="auto",
        kind="blocked", proposal_id=None, reason="噪音词条", detail={},
        impact_stats={}, applied_by="system", created_at=None,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


class _FakeDB:
    def __init__(self, row=None, scalar_values=None):
        self.row = row
        self._scalars = iter(scalar_values or [])
        self.added = []
        self.committed = 0

    async def get(self, model, obj_id):
        return self.row

    async def scalar(self, stmt):
        return next(self._scalars, 0)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed += 1


_ADMIN = {"sub": "admin"}


@pytest.fixture()
def dyn_spy(monkeypatch):
    """dynamic_filters 间谍：记录调用并返回可控行为。"""
    state = {"adds": [], "removes": [], "blocked": False, "remove_result": True}
    monkeypatch.setattr(
        dg.dyn, "add_entry",
        lambda kind, term, **kw: state["adds"].append((kind, term)),
    )
    monkeypatch.setattr(
        dg.dyn, "remove_entry",
        lambda kind, term: (state["removes"].append((kind, term)), state["remove_result"])[1],
    )
    monkeypatch.setattr(dg.dyn, "is_dynamically_blocked", lambda term: state["blocked"])
    monkeypatch.setattr(dg, "_cleanup_skill_nodes", lambda term: 3)
    return state


# ── 审核基础校验 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_review_rejects_missing_reason(dyn_spy):
    resp = await dg.review_proposal("p1", {"action": "approve", "reason": "  "}, _FakeDB(_proposal()), _ADMIN)
    assert resp.status_code == 422 and json.loads(resp.body)["code"] == 4000


@pytest.mark.asyncio
async def test_review_rejects_unknown_action(dyn_spy):
    resp = await dg.review_proposal("p1", {"action": "maybe", "reason": "r"}, _FakeDB(_proposal()), _ADMIN)
    assert json.loads(resp.body)["code"] == 4000


@pytest.mark.asyncio
async def test_review_missing_proposal_404(dyn_spy):
    resp = await dg.review_proposal("pX", {"action": "approve", "reason": "r"}, _FakeDB(None), _ADMIN)
    assert resp.status_code == 404 and json.loads(resp.body)["code"] == 4040


@pytest.mark.asyncio
async def test_review_non_pending_conflict(dyn_spy):
    resp = await dg.review_proposal(
        "p1", {"action": "approve", "reason": "r"}, _FakeDB(_proposal(status="approved")), _ADMIN)
    assert json.loads(resp.body)["code"] == 4090


# ── approve 执行语义 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_approve_add_stopword_applies_blocked_and_cleans(dyn_spy):
    db = _FakeDB(_proposal())
    resp = await dg.review_proposal("p1", {"action": "approve", "reason": "确认噪音"}, db, _ADMIN)
    assert resp.code == 0 and resp.data["status"] == "approved"
    assert dyn_spy["adds"] == [("blocked", "低代码平台搭建")]
    assert dyn_spy["removes"] == []
    kinds = [(o.term, o.kind, o.source) for o in db.added if hasattr(o, "kind")]
    assert kinds == [("低代码平台搭建", "blocked", "manual")]
    audits = [o for o in db.added if hasattr(o, "action") and getattr(o, "action", "").startswith("admin.")]
    assert audits and audits[0].action == "admin.dict_guard.approve"
    # auto 路径同语义：清理同名 Skill 节点并计入影响面
    assert db.row.impact_stats["removed_nodes"] == 3


@pytest.mark.asyncio
async def test_approve_remove_dynamic_blocked_removes_entry(dyn_spy):
    dyn_spy["blocked"] = True
    db = _FakeDB(_proposal(action="remove_stopword", term="微"))
    resp = await dg.review_proposal("p1", {"action": "approve", "reason": "误杀"}, db, _ADMIN)
    assert resp.code == 0
    assert dyn_spy["removes"] == [("blocked", "微")]
    assert [o.kind for o in db.added if hasattr(o, "kind")] == ["blocked"]


@pytest.mark.asyncio
async def test_approve_remove_static_without_victim_conflict(dyn_spy):
    db = _FakeDB(_proposal(action="remove_stopword", term="微", evidence=[]))
    resp = await dg.review_proposal("p1", {"action": "approve", "reason": "r"}, db, _ADMIN)
    body = json.loads(resp.body)
    assert body["code"] == 4090 and "git 固化流程" in body["msg"]
    assert dyn_spy["adds"] == [] and dyn_spy["removes"] == []


@pytest.mark.asyncio
async def test_approve_remove_static_with_victim_protects_victim(dyn_spy):
    evidence = [{"label": "受影响技能", "value": "微信小程序"}]
    db = _FakeDB(_proposal(action="remove_stopword", term="微", evidence=evidence))
    resp = await dg.review_proposal("p1", {"action": "approve", "reason": "误杀真实技能"}, db, _ADMIN)
    assert resp.code == 0
    # 静态词不动 git 词表，以受影响技能的动态 protect 落地
    assert dyn_spy["adds"] == [("protected", "微信小程序")]
    assert [o.term for o in db.added if hasattr(o, "kind")] == ["微信小程序"]


@pytest.mark.asyncio
async def test_approve_protect_whitelist_adds_protected(dyn_spy):
    db = _FakeDB(_proposal(action="protect_whitelist", term="微信小程序"))
    resp = await dg.review_proposal("p1", {"action": "approve", "reason": "真实技能"}, db, _ADMIN)
    assert resp.code == 0
    assert dyn_spy["adds"] == [("protected", "微信小程序")]


@pytest.mark.asyncio
async def test_reject_marks_status_without_side_effects(dyn_spy):
    db = _FakeDB(_proposal())
    resp = await dg.review_proposal("p1", {"action": "reject", "reason": "证据不足"}, db, _ADMIN)
    assert resp.code == 0 and resp.data["status"] == "rejected"
    assert dyn_spy["adds"] == [] and dyn_spy["removes"] == []
    # reject 无动态层变更，不写 DictChangeLog（仅 AuditLog）
    assert not [o for o in db.added if hasattr(o, "kind")]
    assert len(db.added) == 1


# ── 回滚 ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rollback_blocked_removes_and_writes_rollback_log(dyn_spy):
    db = _FakeDB(_changelog(), scalar_values=[0])
    resp = await dg.rollback_change("c1", db, _ADMIN)
    assert resp.code == 0
    assert dyn_spy["removes"] == [("blocked", "低代码平台搭建")]
    rollback_logs = [o for o in db.added if getattr(o, "source", "") == "rollback"]
    assert rollback_logs and rollback_logs[0].detail["original_id"] == "c1"


@pytest.mark.asyncio
async def test_rollback_guard_rejects_rollback_record(dyn_spy):
    db = _FakeDB(_changelog(action="rollback"), scalar_values=[0])
    resp = await dg.rollback_change("c1", db, _ADMIN)
    assert json.loads(resp.body)["code"] == 4090


@pytest.mark.asyncio
async def test_rollback_guard_rejects_double_rollback(dyn_spy):
    db = _FakeDB(_changelog(), scalar_values=[1])
    resp = await dg.rollback_change("c1", db, _ADMIN)
    assert json.loads(resp.body)["code"] == 4090 and "已回滚过" in json.loads(resp.body)["msg"]


@pytest.mark.asyncio
async def test_rollback_blocked_missing_entry_conflict(dyn_spy):
    dyn_spy["remove_result"] = False
    db = _FakeDB(_changelog(), scalar_values=[0])
    resp = await dg.rollback_change("c1", db, _ADMIN)
    assert json.loads(resp.body)["code"] == 4090


# ── 报告 ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_report_latest_reads_newest(tmp_path, monkeypatch):
    (tmp_path / "dict_guard_2026-08-20.json").write_text('{"run_date": "2026-08-20"}', encoding="utf-8")
    (tmp_path / "dict_guard_2026-08-21.json").write_text('{"run_date": "2026-08-21"}', encoding="utf-8")
    monkeypatch.setattr(dg, "_REPORT_DIR", tmp_path)
    resp = await dg.latest_report()
    assert resp.code == 0 and resp.data["run_date"] == "2026-08-21"


@pytest.mark.asyncio
async def test_report_latest_404_when_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(dg, "_REPORT_DIR", tmp_path)
    resp = await dg.latest_report()
    assert resp.status_code == 404 and json.loads(resp.body)["code"] == 4040
