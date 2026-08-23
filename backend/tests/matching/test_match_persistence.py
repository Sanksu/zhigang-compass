"""匹配持久化单元测试（设计文档 §11.4.1）。

用 FakeDb 桩覆盖：_persist_match_result_db 幂等 upsert（match_id 唯一，
重复生成更新而非追加）、_persist_feedback 追加记录、_persist_rejected_change
审核驳回变更记录。均只验证 _persist_* 函数行为，不触真实数据库。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.api.v1.admin import _persist_rejected_change
from app.api.v1 import match as match_api
from app.api.v1.match import (
    _load_match_result,
    _match_task_cache_owned,
    _persist_feedback,
    _persist_match_result_db,
)
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


class TestMatchTaskCacheOwned:
    """Redis match:task 回退分支归属校验（代码审查：越权防护缺失）。

    该快照由 _persist_match_result（compare 同步路径）写入，内容无 user_id，
    只能以其 match:result 快照归属校验——UUID 可猜度下防止横向越权。
    """

    def test_owner_passes(self):
        async def _run():
            with patch(
                "app.api.v1.match._load_match_result",
                new=AsyncMock(return_value={"match_id": "m1"}),
            ):
                assert await _match_task_cache_owned({"match_id": "m1"}, "u1") is True

        asyncio.run(_run())

    def test_other_user_rejected(self):
        # 他人任务（match:result 归属校验失败返回 None）→ 拒绝
        async def _run():
            with patch(
                "app.api.v1.match._load_match_result",
                new=AsyncMock(return_value=None),
            ):
                assert await _match_task_cache_owned({"match_id": "m1"}, "u2") is False

        asyncio.run(_run())

    def test_missing_match_id_rejected(self):
        # 无 match_id 的快照无法校验归属 → 拒绝（防伪造快照绕过校验）
        async def _run():
            assert await _match_task_cache_owned({}, "u1") is False

        asyncio.run(_run())


class TestLoadMatchResultDurableFallback:
    """_load_match_result 耐久回退（08-23 闭环收敛 P1-5：双写单读修复）。

    Redis 过期/丢失 → match_results 副本回读（表级归属校验）+ 回填缓存；
    无副本或空 result → None（404）。Redis 命中路径行为不变。
    """

    @staticmethod
    def _make_factory(row):
        """async_session_factory 替身：async context manager 包 FakeDb。"""

        class _Ctx:
            async def __aenter__(self):
                return _FakeDb(rows=[row] if row is not None else [])

            async def __aexit__(self, *args):
                return False

        return lambda: _Ctx()

    def test_redis_miss_falls_back_to_pg_and_rewarms_cache(self):
        async def _run():
            record = SimpleNamespace(
                result={"match_id": "m1", "total_score": 0.8, "user_id": "u1"}
            )
            redis_mock = AsyncMock()
            redis_mock.get.return_value = None
            with patch.object(
                match_api, "redis_client", new=redis_mock
            ), patch(
                "app.api.v1.match.async_session_factory",
                new=self._make_factory(record),
            ):
                data = await _load_match_result("m1", "u1")

            assert data == record.result
            redis_mock.set.assert_awaited_once()

        asyncio.run(_run())

    def test_redis_miss_without_record_returns_none(self):
        async def _run():
            redis_mock = AsyncMock()
            redis_mock.get.return_value = None
            with patch.object(
                match_api, "redis_client", new=redis_mock
            ), patch(
                "app.api.v1.match.async_session_factory",
                new=self._make_factory(None),
            ):
                data = await _load_match_result("m1", "u1")

            assert data is None
            redis_mock.set.assert_not_awaited()

        asyncio.run(_run())

    def test_redis_miss_with_empty_result_returns_none(self):
        """副本 result 为空 dict → 视为无有效数据（404），不回填缓存。"""

        async def _run():
            record = SimpleNamespace(result={})
            redis_mock = AsyncMock()
            redis_mock.get.return_value = None
            with patch.object(
                match_api, "redis_client", new=redis_mock
            ), patch(
                "app.api.v1.match.async_session_factory",
                new=self._make_factory(record),
            ):
                data = await _load_match_result("m1", "u1")

            assert data is None
            redis_mock.set.assert_not_awaited()

        asyncio.run(_run())
