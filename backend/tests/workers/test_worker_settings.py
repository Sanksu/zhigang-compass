"""ARQ Worker 注册测试（BE-M3-04）。

验证 WorkerSettings 注册了全部任务函数（防止任务名写错/遗漏导致
入队后无人消费），且各任务均为 async 可调用。
"""

import inspect

from app.workers.tasks import (
    WorkerSettings,
    aggregate_positions,
    batch_extract,
    check_data_freshness,
    crawl_platform,
    cross_validate_jds,
    detect_inflation,
    discovery_daily,
    diversity_report,
    evaluate_courses,
    evolution_compute,
    load_courses,
    resume_parse,
    run_etl_pipeline,
    validate_temporal,
)

EXPECTED_FUNCTIONS = [
    crawl_platform,
    run_etl_pipeline,
    validate_temporal,
    detect_inflation,
    resume_parse,
    batch_extract,
    load_courses,
    evaluate_courses,
    diversity_report,
    check_data_freshness,
    aggregate_positions,
    cross_validate_jds,
    discovery_daily,
    evolution_compute,
]


def test_worker_settings_registers_all_tasks():
    assert WorkerSettings.functions == EXPECTED_FUNCTIONS


def test_all_tasks_are_async_callables():
    for fn in EXPECTED_FUNCTIONS:
        assert inspect.iscoroutinefunction(fn), f"{fn.__name__} 不是 async 函数"


def test_etl_pipeline_stages_cover_all_quality_tasks():
    """run_etl_pipeline 的 11 个阶段与检测/聚合/验证任务一一对应。"""
    from app.workers import tasks as t

    src = inspect.getsource(t.run_etl_pipeline)
    for stage_fn in ("crawl_platform", "validate_temporal", "detect_inflation",
                     "batch_extract", "load_courses", "evaluate_courses",
                     "aggregate_positions", "cross_validate_jds",
                     "diversity_report", "check_data_freshness"):
        assert f"await {stage_fn}" in src, f"run_etl_pipeline 缺少阶段 {stage_fn}"


def test_worker_settings_has_redis_config():
    assert WorkerSettings.max_retries == 2
    assert WorkerSettings.redis_settings is not None
