"""全审批池只读汇总端点测试（approval_summary）：assemble 纯函数 + 端点协程。

项目纯函数风格：asyncio.run 直调，注入 fake async session（_count 仅需
db.scalar(stmt) 按固定调用序返回计数）。
"""

import asyncio

from app.api.v1.admin_routes import approval_summary as mod


def test_assemble_汇总求和并按待办降序():
    streams = [
        {"id": "evolution", "pending": 2, "review": 0, "approved": 31},
        {"id": "candidate_promotion", "pending": 8, "review": 4, "approved": 58},
        {"id": "dict_guard", "pending": 3, "review": 0, "approved": 18},
    ]
    out = mod.assemble(streams)
    assert out["summary"] == {"total_pending": 13, "total_review": 4, "total_approved": 107}
    # 按 pending 降序；pending 相同按 id 稳定排序
    assert [s["id"] for s in out["streams"]] == [
        "candidate_promotion", "dict_guard", "evolution",
    ]
    # 输入不被原地改写（纯函数）
    assert [s["id"] for s in streams] == ["evolution", "candidate_promotion", "dict_guard"]


def test_assemble_空流返回零汇总():
    out = mod.assemble([])
    assert out == {"summary": {"total_pending": 0, "total_review": 0, "total_approved": 0}, "streams": []}


class _FakeCountSession:
    """按调用顺序弹计数：_count 仅调 db.scalar(stmt)，固定 16 次调用序。"""

    def __init__(self, counts):
        self._counts = list(counts)
        self.calls = 0

    async def scalar(self, stmt):
        value = self._counts[self.calls]
        self.calls += 1
        return value


def test_approval_summary_端点返回契约结构与汇总():
    # 16 次 count 调用序（对齐端点内书写顺序）：
    # 候选 pending/review/approved → 演化 pending/approved → 归档 pending/approved
    # → 字典 pending/approved → LLM pending/review/approved → 观察池 pending/approved
    # → 别名 pending/approved
    counts = [8, 4, 58, 2, 31, 1, 25, 3, 18, 5, 1, 9, 1, 7, 2, 10]
    resp = asyncio.run(mod.approval_summary(db=_FakeCountSession(counts)))

    data = resp.data if hasattr(resp, "data") else resp["data"]
    assert data["summary"] == {
        "total_pending": 8 + 2 + 1 + 3 + 5 + 1 + 2,
        "total_review": 4 + 1,
        "total_approved": 58 + 31 + 25 + 18 + 9 + 7 + 10,
    }
    streams = {s["id"]: s for s in data["streams"]}
    assert len(streams) == 7
    assert streams["candidate_promotion"]["pending"] == 8
    assert streams["candidate_promotion"]["review"] == 4
    assert streams["llm_decisions"]["review"] == 1
    # 深链路由口径（原审核页对应 Tab / 独立路由）
    assert streams["candidate_promotion"]["route"] == "/admin/review?tab=candidate"
    assert streams["dict_guard"]["route"] == "/admin/review/dict"
    assert streams["tech_watch"]["route"] == "/admin/review/watch"