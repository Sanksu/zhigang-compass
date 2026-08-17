"""岗位待审核队列端点测试（08-15 修复：state 缺省 candidate 过滤）。

背景：positions_pending 注释声明"默认返回 candidate"，但实现 state 缺省
不过滤——摘要/徽标把已晋升 emerging/stable 计入"待审核"（29 条中真待办
仅 2 条 candidate）。修复后缺省只查 candidate，显式传 state 仍可查其他。
"""

import asyncio
import inspect

from app.api.v1.admin import positions_pending


class _FakeDB:
    """AsyncSession 桩：捕获最后一次 stmt，count 与 rows 可分离预设。"""

    def __init__(self, rows: list, total: int | None = None):
        self._rows = rows
        self._total = total if total is not None else len(rows)
        self.last_stmt = None

    async def scalar(self, stmt):
        self.last_stmt = stmt
        return self._total

    async def scalars(self, stmt):
        self.last_stmt = stmt
        return self._rows


def _call(db: _FakeDB, **kwargs):
    """同步包装（项目无 pytest-asyncio auto 模式，参照 workers 测试惯例）。"""

    async def run():
        return await positions_pending(
            page=kwargs.get("page", 1),
            size=kwargs.get("size", 20),
            db=db,
            **({"state": kwargs["state"]} if "state" in kwargs else {}),
        )

    return asyncio.run(run())


def _state_param(db: _FakeDB) -> str | None:
    """SQLAlchemy 参数化 where 的绑定值（str(stmt) 不内联参数值）。"""
    return db.last_stmt.compile().params.get("state_1")


class TestPositionsPending:
    def test_default_state_declares_candidate(self):
        """Query 缺省值声明为 candidate（直调函数不经 FastAPI 注入，查签名）。"""
        default = inspect.signature(positions_pending).parameters["state"].default
        assert default.default == "candidate"

    def test_filters_by_candidate(self):
        """state=candidate → 绑定参数 state=candidate（待审核口径）。"""
        db = _FakeDB([])
        _call(db, state="candidate")
        assert _state_param(db) == "candidate"

    def test_explicit_state_emerging(self):
        """显式 state=emerging → 绑定参数 state=emerging（管理员查看已晋升）。"""
        db = _FakeDB([])
        _call(db, state="emerging")
        assert _state_param(db) == "emerging"

    def test_total_reflects_filtered_count(self):
        """total = 过滤后行数（摘要徽标口径）。"""
        db = _FakeDB([], total=2)
        res = _call(db, state="candidate")
        assert res.data["total"] == 2
