"""ARQ Worker 注册测试（BE-M3-04）。

验证 WorkerSettings 注册了全部任务函数（防止任务名写错/遗漏导致
入队后无人消费），且各任务均为 async 可调用。
"""

import asyncio
import inspect

from app.workers.settings import WorkerSettings
from app.workers.tasks import (
    _run_limited_stage,
    _run_stage,
    aggregate_positions,
    backfill_embeddings,
    batch_extract,
    check_data_freshness,
    check_llm_providers_health,
    generate_diagnosis,
    graph_health_check,
    crawl_platform,
    crawl_scheduler,
    cross_validate_jds,
    dedup_simhash,
    detect_inflation,
    dict_guard_daily,
    discovery_auto_transition,
    discovery_daily,
    diversity_report,
    enrich_course_skills,
    evaluate_courses,
    load_courses,
    match_recommend,
    resume_parse,
    run_etl_job_manual,
    run_etl_pipeline,
    run_etl_pipeline_scheduled,
    snapshot_graph,
    sync_skill_normalization,
    validate_temporal,
    watch_signal_daily,
)

EXPECTED_FUNCTIONS = [
    crawl_platform,
    crawl_scheduler,
    run_etl_pipeline,
    run_etl_pipeline_scheduled,
    run_etl_job_manual,
    dedup_simhash,
    validate_temporal,
    detect_inflation,
    resume_parse,
    match_recommend,
    generate_diagnosis,
    batch_extract,
    enrich_course_skills,
    load_courses,
    evaluate_courses,
    diversity_report,
    check_data_freshness,
    aggregate_positions,
    cross_validate_jds,
    sync_skill_normalization,
    backfill_embeddings,
    discovery_daily,
    discovery_auto_transition,
    watch_signal_daily,
    snapshot_graph,
    dict_guard_daily,
    check_llm_providers_health,
    graph_health_check,
]


def _name(fn):
    """Function 实例（arq.worker.func 包装）取 .name，普通函数取 __qualname__。"""
    return fn.name if hasattr(fn, "name") else fn.__qualname__


def _coroutine(fn):
    """Function 实例解包为原始协程函数。"""
    return fn.coroutine if hasattr(fn, "coroutine") else fn


def test_worker_settings_registers_all_tasks():
    assert [_name(f) for f in WorkerSettings.functions] == [
        f.__qualname__ for f in EXPECTED_FUNCTIONS
    ]


def test_all_tasks_are_async_callables():
    for fn in EXPECTED_FUNCTIONS:
        assert inspect.iscoroutinefunction(_coroutine(fn)), f"{_name(fn)} 不是 async 函数"


def test_etl_pipeline_stages_cover_all_quality_tasks():
    """run_etl_pipeline 的 12 个阶段与检测/聚合/验证/快照任务一一对应。"""
    from app.workers import tasks as t

    src = inspect.getsource(t.run_etl_pipeline)
    for stage_fn in ("crawl_platform", "dedup_simhash", "validate_temporal", "detect_inflation",
                     "batch_extract", "load_courses", "evaluate_courses",
                     "aggregate_positions", "cross_validate_jds",
                     "diversity_report", "check_data_freshness",
                     "snapshot_graph"):
        assert stage_fn in src, f"run_etl_pipeline 缺少阶段 {stage_fn}"


def test_worker_settings_has_redis_config():
    assert WorkerSettings.max_retries == 2
    assert WorkerSettings.redis_settings is not None


def test_worker_settings_job_timeout_not_task_timeout():
    """arq 参数名是 job_timeout（task_timeout 会被忽略，默认 300s 杀掉长任务）。

    回归：坑 22——长任务（爬虫/批量 LLM 抽取）需 30 分钟窗口。
    """
    assert getattr(WorkerSettings, "job_timeout", None) == 1800
    assert not hasattr(WorkerSettings, "task_timeout")


def test_etl_pipeline_has_per_function_timeout():
    """ETL 主管线 per-function 超时 3h / 不重试。

    回归：arq enqueue 不接收 _timeout/_max_tries（传了会被当作任务参数导致
    TypeError），超时/重试须在 WorkerSettings.functions 用 func() 配置。
    """
    etl = next(
        f for f in WorkerSettings.functions if _name(f) == run_etl_pipeline.__qualname__
    )
    assert etl.timeout_s == 10800
    assert etl.max_tries == 1


def test_etl_scheduled_has_per_function_timeout():
    """容器内 ETL cron 入口同样按 3h 超时（主管线可跑数小时）。"""
    etl = next(
        f
        for f in WorkerSettings.functions
        if _name(f) == run_etl_pipeline_scheduled.__qualname__
    )
    assert etl.timeout_s == 10800
    assert etl.max_tries == 1


def test_etl_job_manual_has_per_function_timeout():
    """管理端手动触发包装按 3h 超时（run_etl_pipeline 白名单目标可跑数小时）。"""
    etl = next(
        f
        for f in WorkerSettings.functions
        if _name(f) == run_etl_job_manual.__qualname__
    )
    assert etl.timeout_s == 10800
    assert etl.max_tries == 1


def test_crawl_platform_has_per_function_timeout():
    """crawl_platform 显式放宽超时 2h（H2 修复）。

    回归：全局 job_timeout=1800s 会在 zhilian 独立触发合法采集（最长 7200s）
    完成前 kill 掉 ARQ 任务；crawl_platform 需按 per-function timeout 放宽。
    """
    cp = next(
        f for f in WorkerSettings.functions if _name(f) == crawl_platform.__qualname__
    )
    assert cp.timeout_s == 7200
    assert cp.max_tries == 1


def test_worker_cron_jobs_register_etl_schedule():
    """WorkerSettings.cron_jobs 注册 ETL 主管线 cron（时间来自 runtime_config）。"""
    from app.core import runtime_config as rc
    from arq.cron import CronJob

    etl_cron = next(
        c for c in WorkerSettings.cron_jobs if c.coroutine == run_etl_pipeline_scheduled
    )
    assert isinstance(etl_cron, CronJob)
    assert etl_cron.hour == rc.get("etl_run_hour", 5)
    assert etl_cron.minute == rc.get("etl_run_minute", 0)
    assert etl_cron.run_at_startup is False


# ── _run_stage 阶段隔离（08-14 修复：evolved_from 崩溃拖垮 ETL 实证）──

def test_run_stage_success():
    """成功阶段返回原始结果。"""
    async def _ok():
        return {"status": "ok"}
    assert asyncio.run(_run_stage("t", _ok())) == {"status": "ok"}


def test_run_stage_failure_isolated():
    """失败阶段记录 error dict，不抛异常（不阻塞后续阶段）。"""
    async def _boom():
        raise ValueError("测试异常")
    result = asyncio.run(_run_stage("t", _boom()))
    assert isinstance(result, dict) and "error" in result
    assert "ValueError" in result["error"]


def test_limited_stage_isolates_limit_lookup_failure(monkeypatch):
    """积压量查询失败也属于阶段失败，不得终止整条 ETL。"""
    async def _limit(*args, **kwargs):
        raise RuntimeError("数据库不可用")

    async def _task(ctx, limit):
        raise AssertionError("限额查询失败时不应执行任务")

    monkeypatch.setattr("app.workers.tasks._etl_limit", _limit)
    result = asyncio.run(
        _run_limited_stage(
            "limited", extracted=True, default=100, task=_task, ctx={}
        )
    )
    assert "RuntimeError" in result["error"]
