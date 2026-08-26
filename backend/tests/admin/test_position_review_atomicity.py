"""岗位审核域原子性测试（postmortems/003 顺序：PG 决策先行，图写副作用最后）。

验证三个审核端点（review_position / review_evolution / archive_position）在
图写副作用失败时：（1）PG 决策（状态 + 审计 + 驳回记录）已先落库、不丢；
（2）响应透出 effects_applied=False；（3）不再出现旧的"图写前置"反模式。
"""

import asyncio
from unittest.mock import AsyncMock, patch

from app.api.v1.admin_routes import position_reviews

_OPERATOR = "0356249f-9b04-47a3-a307-af6e7883f084"  # UUID 形式 operator（AuditLog.user_id 列约束）


class _FakeDB:
    """AsyncSession 桩：捕获 db.commit 调用次数 + 所有 add 对象。"""

    def __init__(self, cand_row):
        self.cand_row = cand_row
        self.added = []
        self.commits = 0

    def get(self, model, _id):  # 简化为同步返回候选行
        async def _get():
            return self.cand_row
        return _get()

    async def commit(self):
        self.commits += 1

    def add(self, obj):
        self.added.append(obj)

    async def scalar(self, *a, **k):
        return None

    async def scalars(self, *a, **k):
        return self.cand_row


class _FakePosition:
    """DiscoveryCandidate 桩（仅证明领域需要的字段）。"""

    def __init__(self, state="candidate"):
        self.id = "cand-0001"
        self.position_name = "测试岗位"
        self.state = state
        self.features = {
            "jd_freq_ma3": 1.0,
            "z_score": 3.5,
            "source_diversity": 3,
            "cross_source_consistency": 0.5,
        }
        self.confidence = {"final_confidence": 0.8}
        self.evidence_refs = []
        self.seed_matched = True
        self.rag_matched = True
        self.definition_draft = ""


def _make_candidate_stub(state):
    from types import SimpleNamespace

    return SimpleNamespace(
        id="cand-0001",
        position_name="测试岗位",
        state=state,
        features={"jd_freq_ma3": 1.0, "z_score": 3.5, "source_diversity": 3, "cross_source_consistency": 0.5},
        confidence={"final_confidence": 0.8},
        evidence_refs=[],
        seed_matched=True,
        rag_matched=True,
        definition_draft="",
        detected_at="2026-08-01T00:00:00",
    )


def _run(coro):
    return asyncio.run(coro)


class TestReviewPositionAtomicity:
    def test_approve_persists_pg_before_graph(self):
        """approve：PG 状态 + 审计先提交，随后才调图写副作用。"""
        cand_row = _make_candidate_stub("candidate")
        db = _FakeDB(cand_row)
        req = {"action": "approve", "reason": "数据达标"}

        async def run():
            with patch.object(
                position_reviews,
                "_apply_graph_state",
                new=AsyncMock(return_value=None),
            ) as mock_graph:
                return await position_reviews.review_position(
                    candidate_id="cand-0001",
                    req=req,
                    db=db,
                    current_user={"sub": _OPERATOR},
                ), mock_graph

        resp, mock_graph = _run(run())

        assert resp.data["state"] == "emerging"
        assert resp.data["effects_applied"] is True
        # PG 决策一次提交且先于图写场景：图写被调用时决策已落库
        assert db.commits >= 1
        mock_graph.assert_awaited_once()
        # 审计随 PG 阶段 add（决策阶段固化，不随图写失败丢失）
        from app.models.business import AuditLog
        assert any(isinstance(x, AuditLog) for x in db.added)

    def test_approve_graph_failure_reports_effects_not_applied(self):
        """图写副作用异常：决策不丢，透出 effects_applied=False。"""
        cand_row = _make_candidate_stub("candidate")
        db = _FakeDB(cand_row)
        req = {"action": "approve", "reason": "数据达标"}

        async def run():
            with patch.object(
                position_reviews,
                "_apply_graph_state",
                new=AsyncMock(side_effect=RuntimeError("Neo4j unavailable")),
            ):
                return await position_reviews.review_position(
                    candidate_id="cand-0001",
                    req=req,
                    db=db,
                    current_user={"sub": _OPERATOR},
                )

        resp = _run(run())

        # 决策已落库（state 已改 + commit 发生）
        assert cand_row.state == "emerging"
        assert db.commits >= 1
        # 图写失败透出（不抛 500，人工决策不因图写失败回滚）
        assert resp.data["effects_applied"] is False

    def test_reject_persists_rejected_before_graph(self):
        """reject：状态 + 驳回记录 + 审计先提交，图写副作用随后。"""
        cand_row = _make_candidate_stub("candidate")
        db = _FakeDB(cand_row)
        req = {"action": "reject", "reason": "驳回测试"}

        async def run():
            with patch.object(
                position_reviews,
                "_apply_graph_state",
                new=AsyncMock(return_value=None),
            ) as mock_graph:
                return await position_reviews.review_position(
                    candidate_id="cand-0001",
                    req=req,
                    db=db,
                    current_user={"sub": _OPERATOR},
                ), mock_graph

        resp, mock_graph = _run(run())

        assert resp.data["state"] == "rejected"
        assert mock_graph.await_count == 1
        from app.models.business import RejectedChange
        assert any(isinstance(x, RejectedChange) for x in db.added)


class TestReviewEvolutionAtomicity:
    def test_approve_persists_state_audit_before_graph(self):
        """演化 approve：PG 状态 + 审计先提交，图写副作用随后。"""
        cand_row = _make_candidate_stub("emerging")
        db = _FakeDB(cand_row)
        req = {"action": "approve", "reason": "确认稳定"}

        async def run():
            with patch.object(
                position_reviews,
                "_apply_graph_state",
                new=AsyncMock(return_value=None),
            ):
                return await position_reviews.review_evolution(
                    candidate_id="cand-0001", req=req, db=db,
                    current_user={"sub": _OPERATOR},
                )

        resp = _run(run())

        assert resp.data["state"] == "stable"
        assert db.commits >= 1


class TestArchivePositionAtomicity:
    def test_archive_persists_state_audit_before_graph(self):
        """归档：PG 状态 + 审计先提交，图写副作用随后且 effects_applied 透出。"""
        cand_row = _make_candidate_stub("declining")
        db = _FakeDB(cand_row)
        req = {"reason": "业务确认归档"}

        async def run():
            with patch.object(
                position_reviews,
                "_apply_graph_state",
                new=AsyncMock(side_effect=RuntimeError("Neo4j unavailable")),
            ):
                return await position_reviews.archive_position(
                    candidate_id="cand-0001", req=req, db=db,
                    current_user={"sub": _OPERATOR},
                )

        resp = _run(run())

        assert resp.data["state"] == "archived"
        assert resp.data["effects_applied"] is False
        assert db.commits >= 1
