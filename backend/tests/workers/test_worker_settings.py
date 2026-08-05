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
    check_llm_providers_health,
    crawl_platform,
    cross_validate_jds,
    dedup_simhash,
    detect_inflation,
    discovery_daily,
    discovery_auto_transition,
    diversity_report,
    evaluate_courses,
    evolution_compute,
    load_courses,
    resume_parse,
    run_etl_pipeline,
    snapshot_graph,
    validate_temporal,
)

EXPECTED_FUNCTIONS = [
    crawl_platform,
    run_etl_pipeline,
    dedup_simhash,
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
    discovery_auto_transition,
    snapshot_graph,
    evolution_compute,
    check_llm_providers_health,
]


def test_worker_settings_registers_all_tasks():
    assert WorkerSettings.functions == EXPECTED_FUNCTIONS


def test_all_tasks_are_async_callables():
    for fn in EXPECTED_FUNCTIONS:
        assert inspect.iscoroutinefunction(fn), f"{fn.__name__} 不是 async 函数"


def test_etl_pipeline_stages_cover_all_quality_tasks():
    """run_etl_pipeline 的 12 个阶段与检测/聚合/验证/快照任务一一对应。"""
    from app.workers import tasks as t

    src = inspect.getsource(t.run_etl_pipeline)
    for stage_fn in ("crawl_platform", "dedup_simhash", "validate_temporal", "detect_inflation",
                     "batch_extract", "load_courses", "evaluate_courses",
                     "aggregate_positions", "cross_validate_jds",
                     "diversity_report", "check_data_freshness",
                     "snapshot_graph"):
        assert f"await {stage_fn}" in src, f"run_etl_pipeline 缺少阶段 {stage_fn}"


def test_worker_settings_has_redis_config():
    assert WorkerSettings.max_retries == 2
    assert WorkerSettings.redis_settings is not None


def test_worker_settings_job_timeout_not_task_timeout():
    """arq 参数名是 job_timeout（task_timeout 会被忽略，默认 300s 杀掉长任务）。

    回归：坑 22——长任务（爬虫/批量 LLM 抽取）需 30 分钟窗口。
    """
    assert getattr(WorkerSettings, "job_timeout", None) == 1800
    assert not hasattr(WorkerSettings, "task_timeout")
