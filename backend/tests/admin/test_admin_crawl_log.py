"""爬虫实时日志 SSE 事件序列测试（_crawl_log_events）。

与 resume 的 _task_stream_events 测试同模式：注入假 get_logs/get_task，
poll_interval=0 快速迭代事件流，验证 log/progress/done/error 各分支。
"""

import json

import pytest

from app.api.v1.admin import _crawl_log_events


def _parse(event: str) -> tuple[str, dict]:
    """SSE 帧 → (event 名, data 对象)。"""
    ev = event.splitlines()[0].removeprefix("event: ")
    data = json.loads(event.split("data: ", 1)[1].strip())
    return ev, data


async def _collect(get_logs, get_task, **kw):
    return [
        e
        async for e in _crawl_log_events(
            "task-1", get_logs, get_task, poll_interval=0, **kw
        )
    ]


class TestCrawlLogEvents:
    @pytest.mark.asyncio
    async def test_log_then_done(self):
        """日志增量推送（offset 递增）→ 终态 success 推 done。"""
        seen = []

        async def get_logs(_tid, start):
            seen.append(start)
            return [f"line{i}" for i in range(start, start + 2)] if start == 0 else []

        states = [
            {"status": "pending", "progress": 0.1},
            {"status": "success", "progress": 1.0},
        ]

        async def get_task(_tid):
            return states.pop(0)

        events = await _collect(get_logs, get_task)
        assert len(events) == 4
        ev, data = _parse(events[0])
        assert ev == "log" and data["line"] == "line0"
        ev, data = _parse(events[1])
        assert ev == "log" and data["line"] == "line1"
        ev, data = _parse(events[2])
        assert ev == "progress" and data["status"] == "pending"
        ev, data = _parse(events[3])
        assert ev == "done" and data["status"] == "success"
        # 日志按 offset 增量拉取，不重复
        assert seen == [0, 2]

    @pytest.mark.asyncio
    async def test_failed_pushes_error(self):
        """任务 failed 推 error（含错误信息）后关闭。"""

        async def get_logs(_tid, _start):
            return []

        async def get_task(_tid):
            return {"status": "failed", "progress": 0.5, "error": "爬虫退出码 1"}

        events = await _collect(get_logs, get_task)
        assert len(events) == 1
        ev, data = _parse(events[0])
        assert ev == "error" and data["error"] == "爬虫退出码 1"

    @pytest.mark.asyncio
    async def test_task_missing_pushes_error(self):
        """任务不存在推 error。"""

        async def get_logs(_tid, _start):
            return []

        async def get_task(_tid):
            return None

        events = await _collect(get_logs, get_task)
        assert len(events) == 1
        ev, data = _parse(events[0])
        assert ev == "error" and "不存在" in data["message"]

    @pytest.mark.asyncio
    async def test_timeout_pushes_error(self):
        """任务长期 running 且超过 timeout 推 error 后关闭。"""

        async def get_logs(_tid, _start):
            return []

        async def get_task(_tid):
            return {"status": "running", "progress": 0.1}

        events = await _collect(get_logs, get_task, timeout=0)
        assert len(events) == 2
        ev, _ = _parse(events[0])
        assert ev == "progress"
        ev, data = _parse(events[1])
        assert ev == "error" and "超时" in data["message"]

    @pytest.mark.asyncio
    async def test_log_fetch_error_does_not_block(self):
        """日志队列读取异常时降级为空，不阻断事件流（终态仍可达）。"""
        called = {"logs": 0}

        async def get_logs(_tid, _start):
            called["logs"] += 1
            raise ConnectionError("redis 不可达")

        states = [{"status": "success", "progress": 1.0}]

        async def get_task(_tid):
            return states.pop(0)

        events = await _collect(get_logs, get_task)
        assert called["logs"] == 1
        assert len(events) == 1
        ev, _ = _parse(events[0])
        assert ev == "done"
