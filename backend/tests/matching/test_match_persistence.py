"""匹配持久化单元测试（设计文档 §11.4.1）。

用 FakeDb 桩覆盖：_persist_match_result_db 幂等 upsert（match_id 唯一，
重复生成更新而非追加）、_persist_feedback 追加记录、_persist_rejected_change
审核驳回变更记录。均只验证 _persist_* 函数行为，不触真实数据库。
"""

import asyncio

from app.api.v1.admin import _persist_rejected_change
from app.api.v1.match import _persist_feedback, _persist_match_result_db
from app.models.business import MatchFeedbackRecord, MatchResultRecord, RejectedChange


class _FakeDb:
    """假 AsyncSession：scalar 返回注入行（无则 None），add 记录到 added，commit 计数。"""

    def __init__(self, rows=None):
        self._rows = rows or []
        self.added = []
        self.commits = 0

    async def scalar(self, stmt):
        return self._rows[0] if self._rows else None

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


class TestPersistMatchResult:
    def test_creates_row_when_absent(self):
        """match_id 不存在 → 新增 MatchResultRecord 行并提交。"""
        async def _run():
            db = _FakeDb()
            await _persist_match_result_db(
                db, "m1", "高级数据分析师", "u1", {"total_score": 0.8, "radar": {}}
            )
            assert db.commits == 1
            assert len(db.added) == 1
            row = db.added[0]
            assert isinstance(row, MatchResultRecord)
            assert row.match_id == "m1"
            assert row.position_name == "高级数据分析师"
            assert row.user_id == "u1"
            assert row.result == {"total_score": 0.8, "radar": {}}

        asyncio.run(_run())

    def test_updates_existing_row_idempotent(self):
        """match_id 已存在 → 更新既有行（不新增、不抛唯一约束冲突）。"""
        async def _run():
            existing = MatchResultRecord(match_id="m1", position_name="旧岗位", user_id="旧用户", result={})
            db = _FakeDb(rows=[existing])
            await _persist_match_result_db(
                db, "m1", "新岗位", "u2", {"total_score": 0.9}
            )
            assert db.commits == 1
            assert db.added == []  # 幂等：未新增行
            assert existing.position_name == "新岗位"
            assert existing.user_id == "u2"
            assert existing.result == {"total_score": 0.9}

        asyncio.run(_run())


class TestPersistFeedback:
    def test_appends_feedback_record(self):
        """反馈落库追加记录（含 comment），非幂等（每次反馈一条）。"""
        async def _run():
            db = _FakeDb()
            await _persist_feedback(db, "m1", 1, "推荐很准")
            assert db.commits == 1
            assert len(db.added) == 1
            row = db.added[0]
            assert isinstance(row, MatchFeedbackRecord)
            assert row.match_id == "m1"
            assert row.score == 1
            assert row.comment == "推荐很准"

        asyncio.run(_run())

    def test_comment_defaults_empty(self):
        async def _run():
            db = _FakeDb()
            await _persist_feedback(db, "m1", -1)
            assert db.added[0].score == -1
            assert db.added[0].comment == ""

        asyncio.run(_run())


class TestPersistRejectedChange:
    def test_records_discovery_rejection(self):
        """审核驳回落库：position_name/change_type/reason 逐字段可追溯。"""
        async def _run():
            db = _FakeDb()
            await _persist_rejected_change(db, "提示工程师", "discovery_reject", "证据不足")
            assert db.commits == 1
            assert len(db.added) == 1
            row = db.added[0]
            assert isinstance(row, RejectedChange)
            assert row.position_name == "提示工程师"
            assert row.change_type == "discovery_reject"
            assert row.reason == "证据不足"

        asyncio.run(_run())
