"""简历 API 编辑与 SSE 推送的纯函数测试（BE-M4-01）。

端点依赖 DB（PUT 编辑落库、SSE 轮询 TaskStatus），测试聚焦纯逻辑：
- `_parse_resume_id`：UUID 校验
- `_merge_fields`：编辑字段顶层覆盖合并语义
- `_task_stream_events`：SSE 事件序列（progress/done/error/不存在/超时），注入假任务查询
"""

import asyncio

import pytest

from app.api.v1.resume import _merge_fields, _parse_resume_id, _task_stream_events


class TestParseResumeId:
    def test_valid_uuid(self):
        rid = "a3b7f0d2-2d5a-4e1c-8f6b-1c3d5e7f9a0b"
        assert _parse_resume_id(rid) == rid

    def test_invalid_returns_none(self):
        assert _parse_resume_id("not-a-uuid") is None
        assert _parse_resume_id("") is None
        assert _parse_resume_id(None) is None


class TestMergeFields:
    def test_overrides_top_level_fields(self):
        merged = _merge_fields({"name": "张三", "skills": ["Python"]}, {"name": "李四"})
        assert merged == {"name": "李四", "skills": ["Python"]}

    def test_empty_parsed_starts_fresh(self):
        assert _merge_fields({}, {"name": "张三"}) == {"name": "张三"}

    def test_original_not_mutated(self):
        original = {"name": "张三"}
        _merge_fields(original, {"total_years": 5})
        assert original == {"name": "张三"}


class TestTaskStreamEvents:
    @staticmethod
    def _payload(status: str, progress: float = 0.0) -> dict:
        return {
            "task_id": "task-1",
            "task_type": "resume_parse",
            "status": status,
            "progress": progress,
            "result": {},
            "error": "",
        }

    @pytest.mark.asyncio
    async def test_success_emits_done(self):
        async def get_task(_):
            return self._payload("success", 1.0)

        events = [e async for e in _task_stream_events("task-1", get_task, poll_interval=0)]
        assert len(events) == 1
        assert events[0].startswith("event: done")

    @pytest.mark.asyncio
    async def test_failed_emits_error(self):
        async def get_task(_):
            return self._payload("failed")

        events = [e async for e in _task_stream_events("task-1", get_task, poll_interval=0)]
        assert len(events) == 1
        assert events[0].startswith("event: error")
        assert "error" in events[0]

    @pytest.mark.asyncio
    async def test_progress_then_done(self):
        states = iter([self._payload("running", 0.5), self._payload("success", 1.0)])

        async def get_task(_):
            return next(states, None)

        events = [e async for e in _task_stream_events("task-1", get_task, poll_interval=0)]
        assert len(events) == 2
        assert events[0].startswith("event: progress")
        assert events[1].startswith("event: done")

    @pytest.mark.asyncio
    async def test_missing_task_emits_error(self):
        async def get_task(_):
            return None

        events = [e async for e in _task_stream_events("task-1", get_task, poll_interval=0)]
        assert len(events) == 1
        assert events[0].startswith("event: error")
        assert "任务不存在" in events[0]

    @pytest.mark.asyncio
    async def test_timeout_emits_error(self):
        async def get_task(_):
            return self._payload("running", 0.1)

        # 超时设 0：首次 progress 后立即触发超时 error
        events = [e async for e in _task_stream_events("task-1", get_task, poll_interval=0, timeout=0)]
        assert len(events) == 2
        assert events[0].startswith("event: progress")
        assert events[1].startswith("event: error")
        assert "推送超时" in events[1]
