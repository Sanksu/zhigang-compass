"""管理端 ETL 手动触发测试（快捷操作面板，契约 /admin/etl/trigger）。

覆盖纯逻辑与包装生命周期：
- API 白名单与 worker 白名单双写点同步（防漂移）
- run_etl_job_manual 生命周期：pending→running→success/failed、未知 job、
  缺 TaskStatus 行、非 dict 返回值降级摘要、error 固定文案不透传内部信息
"""

import asyncio
from types import SimpleNamespace

from app.api.v1.admin_routes import etl as etl_route
from app.api.v1.admin_routes.etl import ETL_JOB_LABELS, EtlTriggerRequest
from app.workers.etl import _MANUAL_JOBS, run_etl_job_manual


def test_api_and_worker_whitelists_in_sync():
    """API 标签表与 worker 白名单双写点必须一致（防新增 job 漏改一侧）。"""
    assert set(ETL_JOB_LABELS) == set(_MANUAL_JOBS)


def test_whitelist_jobs_exist_on_tasks_facade():
    """白名单目标必须是 tasks 门面上真实可调用的函数名（ARQ 按 __qualname__ 匹配）。"""
    from app.workers import tasks as tasks_module

    for name in _MANUAL_JOBS.values():
        assert callable(getattr(tasks_module, name, None)), f"门面缺少任务: {name}"


class _FakeSession:
    def __init__(self, task):
        self._task = task
        self.commits = 0

    async def get(self, model, task_id):
        return self._task

    async def commit(self):
        self.commits += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _patch_session(monkeypatch, task):
    session = _FakeSession(task)
    monkeypatch.setattr(
        "app.core.database.async_session_factory", lambda: session
    )
    return session


def _task():
    return SimpleNamespace(id="tid-1", status="pending", progress=0.0,
                           result={}, error="")


class TestRunEtlJobManual:
    def test_success_updates_lifecycle_and_result(self, monkeypatch):
        task = _task()
        session = _patch_session(monkeypatch, task)

        async def fake_stage(ctx):
            return {"removed": 3}

        module = SimpleNamespace(dedup_simhash=fake_stage)
        result = asyncio.run(run_etl_job_manual({}, "dedup_simhash", "tid-1", tasks_module=module))

        assert result == {"status": "success"}
        assert task.status == "success"
        assert task.progress == 1.0
        assert task.result["job"] == "dedup_simhash"
        assert task.result["removed"] == 3
        assert session.commits >= 2  # running 与 success 各至少一次

    def test_failure_marks_failed_with_fixed_error(self, monkeypatch):
        task = _task()
        _patch_session(monkeypatch, task)

        async def boom(ctx):
            raise RuntimeError("SimHash 内部细节：/srv/secret 路径")

        module = SimpleNamespace(aggregate_positions=boom)
        result = asyncio.run(run_etl_job_manual({}, "aggregate_positions", "tid-1", tasks_module=module))

        assert result == {"status": "failed", "error": "任务执行失败"}
        assert task.status == "failed"
        # 固定文案：内部异常细节仅入服务端日志，不经状态查询端点透出
        assert task.error == "任务执行失败"

    def test_unknown_job_rejects_before_execute(self, monkeypatch):
        task = _task()
        _patch_session(monkeypatch, task)

        module = SimpleNamespace()  # 门面上无任何白名单函数
        result = asyncio.run(run_etl_job_manual({}, "rm_rf", "tid-1", tasks_module=module))

        assert result["status"] == "failed"
        assert task.status == "failed"
        assert task.error == "未知任务类型"

    def test_missing_task_row_returns_without_crash(self, monkeypatch):
        _patch_session(monkeypatch, None)

        async def fake_stage(ctx):
            return {}

        module = SimpleNamespace(dedup_simhash=fake_stage)
        result = asyncio.run(run_etl_job_manual({}, "dedup_simhash", "gone", tasks_module=module))

        assert result == {"status": "failed", "error": "task 不存在"}

    def test_non_dict_result_degrades_to_summary(self, monkeypatch):
        task = _task()
        _patch_session(monkeypatch, task)

        async def weird(ctx):
            return "裸字符串结果"

        module = SimpleNamespace(run_etl_pipeline=weird)
        asyncio.run(run_etl_job_manual({}, "run_etl_pipeline", "tid-1", tasks_module=module))

        assert task.status == "success"
        assert task.result["summary"] == "裸字符串结果"


class TestEtlTriggerRoute:
    @staticmethod
    def _body(resp):
        """ok() 返回 APIResponse 模型、error() 返回 JSONResponse，统一转 dict。"""
        import json

        if hasattr(resp, "body"):
            return json.loads(resp.body)
        return resp.model_dump()

    def test_unknown_job_rejected_without_db_write(self):
        """白名单外 job 直接校验拒绝（不触 DB、不入队）。"""
        resp = asyncio.run(etl_route.etl_trigger(EtlTriggerRequest(job="nope"), db=None))
        body = self._body(resp)
        assert body["code"] != 0
        assert "可选" in body["msg"]

    def test_trigger_creates_task_and_enqueues(self, monkeypatch):
        class _FakeDb:
            def __init__(self):
                self.added = []

            def add(self, obj):
                obj.id = "uuid-1"
                self.added.append(obj)

            async def commit(self):
                pass

            async def refresh(self, obj):
                pass

        captured = {}

        async def fake_enqueue(*args, **kwargs):
            captured["args"] = args
            captured.update(kwargs)

        monkeypatch.setattr(etl_route, "enqueue", fake_enqueue)
        db = _FakeDb()

        resp = asyncio.run(etl_route.etl_trigger(EtlTriggerRequest(job="dedup_simhash"), db=db))
        body = self._body(resp)

        assert body["code"] == 0
        assert body["data"] == {"task_id": "uuid-1", "job": "dedup_simhash", "status": "pending"}
        assert captured["args"] == ("run_etl_job_manual",)
        assert captured["job_name"] == "dedup_simhash"
        assert captured["task_id"] == "uuid-1"
        assert db.added[0].task_type == "etl"
        assert db.added[0].status == "pending"

    def test_enqueue_failure_marks_task_failed(self, monkeypatch):
        class _FakeDb:
            def __init__(self):
                self.tasks = []

            def add(self, obj):
                obj.id = "uuid-2"
                self.tasks.append(obj)

            async def commit(self):
                pass

            async def refresh(self, obj):
                pass

        async def broken_enqueue(*args, **kwargs):
            raise ConnectionError("redis down")

        monkeypatch.setattr(etl_route, "enqueue", broken_enqueue)
        db = _FakeDb()

        resp = asyncio.run(etl_route.etl_trigger(EtlTriggerRequest(job="run_etl_pipeline"), db=db))
        body = self._body(resp)

        assert body["code"] != 0
        assert body["msg"] == "任务入队失败，请稍后重试"
        # 入队失败必须落库可见（failed），不能留 pending 孤儿行
        assert db.tasks[0].status == "failed"
        assert db.tasks[0].error == "任务入队失败"