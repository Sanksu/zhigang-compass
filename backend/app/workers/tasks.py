"""ARQ 任务门面（facade）：WorkerSettings 注册与既有 import 的兼容入口。

任务实现已按职责拆分至：
- ``app.workers.etl_tasks`` — ETL 管线阶段任务 + JD 快照共享辅助函数
- ``app.workers.courses`` — 课程任务（enrich_course_skills / load_courses / evaluate_courses）
- ``app.workers.quality`` — 质量/运维任务（diversity_report / check_data_freshness / graph_health_check）
- ``app.workers.etl`` — ETL 编排（run_etl_pipeline / _run_stage / _etl_limit）

本文件保留：
- ETL 编排薄包装（_etl_limit / _run_limited_stage / run_etl_pipeline，转发 app.workers.etl，
  供 test_worker_settings 的 monkeypatch 锚点与 etl 编排器 tasks_module 解析）
- LLM 健康检查（_alert_llm / check_llm_providers_health，settings.py 的 5min cron 引用）
- 全部任务的 re-export：ARQ 按函数 __qualname__ 匹配（WorkerSettings.functions 由此处导入），
  任务名必须保持不变；既有 ``from app.workers.tasks import X`` 亦由此兼容。

生命周期钩子 on_startup / on_shutdown 已移除——app.workers.settings 定义自己的
钩子（warm_ocr / warm_matching）。
"""

import asyncio
import sys

from app.core import runtime_config
from app.workers.crawl import (
    CDP_SPIDERS as CDP_SPIDERS,
    MAX_RESULTS_SUPPORTED as MAX_RESULTS_SUPPORTED,
    _CRAWL_ENV as _CRAWL_ENV,
    _CRAWL_TIMEOUT_BY_SPIDER as _CRAWL_TIMEOUT_BY_SPIDER,
    _CRAWL_TIMEOUT_SEC as _CRAWL_TIMEOUT_SEC,
    _CRAWLERS_DIR as _CRAWLERS_DIR,
    _OUTPUT_DIR as _OUTPUT_DIR,
    _UTF8_ENV as _UTF8_ENV,
    _crawl_timeout as _crawl_timeout,
    _kill_process_tree as _kill_process_tree,
    crawl_platform as crawl_platform,
    crawl_scheduler as crawl_scheduler,
)
from app.workers.courses import (
    _ENRICH_MAX_FAILS as _ENRICH_MAX_FAILS,
    _ENRICH_RETRY_DELAY_SECONDS as _ENRICH_RETRY_DELAY_SECONDS,
    enrich_course_skills as enrich_course_skills,
    evaluate_courses as evaluate_courses,
    load_courses as load_courses,
)
from app.workers.diagnosis import generate_diagnosis as generate_diagnosis
from app.workers.discovery import (
    _Provider as _Provider,
    _candidate_id as _candidate_id,
    _first_seen_date_of as _first_seen_date_of,
    _position_skill_novelty as _position_skill_novelty,
    _upsert_candidate as _upsert_candidate,
    discovery_auto_transition as discovery_auto_transition,
    discovery_daily as discovery_daily,
    watch_signal_daily as watch_signal_daily,
)
from app.workers.etl import (
    _etl_limit as _etl_limit_impl,
    _run_limited_stage as _run_limited_stage_impl,
    _run_stage as _run_stage,
    run_etl_pipeline as _run_etl_pipeline,
    run_etl_pipeline_scheduled as _run_etl_pipeline_scheduled,
)
from app.workers.etl_tasks import (
    _JD_TEXT_FIELDS as _JD_TEXT_FIELDS,
    _JD_TEXT_MAX_CHARS as _JD_TEXT_MAX_CHARS,
    _QUALITY_LEVELS as _QUALITY_LEVELS,
    _build_jd_text as _build_jd_text,
    _experience_years as _experience_years,
    _extraction_of as _extraction_of,
    _graph_skill_first_seen as _graph_skill_first_seen,
    _history_skill_sets as _history_skill_sets,
    _is_jd_text_short as _is_jd_text_short,
    _publish_date as _publish_date,
    _purge_dup_import_residue as _purge_dup_import_residue,
    _skill_first_seen_days as _skill_first_seen_days,
    _skills_of as _skills_of,
    _snapshot_with_skip as _snapshot_with_skip,
    aggregate_positions as aggregate_positions,
    backfill_embeddings as backfill_embeddings,
    batch_extract as batch_extract,
    cross_validate_jds as cross_validate_jds,
    dedup_simhash as dedup_simhash,
    detect_inflation as detect_inflation,
    snapshot_graph as snapshot_graph,
    sync_skill_normalization as sync_skill_normalization,
    validate_temporal as validate_temporal,
)
from app.workers.matching import (
    _complete_recommend_result as _complete_recommend_result,
    match_recommend as match_recommend,
    resume_parse as resume_parse,
)
from app.workers.quality import (
    check_data_freshness as check_data_freshness,
    diversity_report as diversity_report,
    graph_health_check as graph_health_check,
)
from app.workers.utils import (
    push_crawl_log as _push_crawl_log,  # noqa: F401  # legacy monkeypatch path
    update_crawl_task as _update_crawl_task,  # noqa: F401  # legacy monkeypatch path
)


async def _etl_limit(extracted: bool, default: int) -> int:
    """Compatibility wrapper for the extracted ETL backlog limit helper."""
    return await _etl_limit_impl(extracted, default)


async def _run_limited_stage(
    name: str,
    *,
    extracted: bool,
    default: int,
    task,
    ctx: dict,
    task_kwargs: dict | None = None,
) -> dict:
    """Run a limited ETL stage while preserving tasks-module monkeypatches."""
    return await _run_limited_stage_impl(
        name,
        extracted=extracted,
        default=default,
        task=task,
        ctx=ctx,
        task_kwargs=task_kwargs,
        limit_getter=_etl_limit,
    )


async def run_etl_pipeline(
    ctx: dict,
    run_date: str | None = None,
    skip_cdp: bool = False,
) -> dict:
    """Compatibility entry point for the extracted ETL orchestrator.

    Stages: crawl_platform, dedup_simhash, batch_extract, validate_temporal,
    detect_inflation, enrich_course_skills, load_courses, evaluate_courses,
    aggregate_positions, cross_validate_jds, sync_skill_normalization,
    diversity_report, check_data_freshness, snapshot_graph.
    """
    return await _run_etl_pipeline(
        ctx,
        run_date=run_date,
        skip_cdp=skip_cdp,
        tasks_module=sys.modules[__name__],
    )


async def run_etl_pipeline_scheduled(ctx: dict) -> dict:
    """容器内 ARQ cron 调度入口（当日幂等锁 + 转发主管线，见 app.workers.etl）。"""
    return await _run_etl_pipeline_scheduled(
        ctx,
        tasks_module=sys.modules[__name__],
    )


# ============================================================
# ARQ Worker 注册
# ============================================================

_LLM_ALERT_DEDUP_TTL = 3600  # LLM 告警去重窗口（1 小时，防 5min cron 刷屏）


async def _alert_llm(event: str, message: str) -> bool:
    """LLM 异常告警（Redis SET NX 去重：同事件窗口内只发一次）。

    Redis 不可用时不阻塞告警本身（去重失效可接受——webhook 幂等）。
    """
    from app.core.config import settings
    from app.services.alerting import send_alert

    # 08-16：管理后台可编辑 webhook（runtime_settings.json，重启生效）
    webhook = runtime_config.get("alert_webhook_url") or settings.alert_webhook_url
    if not webhook:
        return False
    key = f"alert:dedup:{event}"
    try:
        import redis as redis_sync

        r = redis_sync.Redis.from_url(settings.redis_url, socket_timeout=3)
        acquired = await asyncio.to_thread(
            r.set, key, "1", nx=True, ex=_LLM_ALERT_DEDUP_TTL
        )
        r.close()
        if not acquired:
            return False  # 同事件已告警（窗口内）
    except Exception:
        pass
    return await send_alert(event, message)


async def check_llm_providers_health(ctx: dict) -> dict:
    """LLM provider 健康检查（设计文档 §6.5：每 5min 调 /models 端点）。

    遍历 enabled provider 探测 /models 可用性，结果写 Redis（llm:health:{name}），
    供调用链展示/运维排查。配置缺失（无 yaml）时跳过并返回原因，不触发
    ARQ 重试；单 provider 探测失败仅记 unhealthy，由熔断/退避机制在调用侧兜底。

    08-15 事故教训（LLM 配置丢失静默降级无人发现）：配置缺失或全部 provider
    不可用 → webhook 告警（1 小时去重），不再静默。
    """
    from app.services.extraction.llm_provider import (
        LLMConfigurationError,
        health_check_all,
    )

    try:
        checked = await asyncio.to_thread(health_check_all)
    except LLMConfigurationError as e:
        alerted = await _alert_llm(
            "llm_config_missing", f"LLM 配置缺失，全链路将降级规则抽取: {e}"
        )
        return {"status": "skipped", "reason": str(e), "alerted": alerted}
    if checked and not any(checked.values()):
        alerted = await _alert_llm(
            "llm_providers_down",
            f"全部 LLM provider 不可用（{len(checked)} 个），抽取将降级规则兜底",
        )
        print(f"[check_llm_providers_health] ALL DOWN {checked}", flush=True)
        return {"status": "degraded", "healthy": checked, "alerted": alerted}
    print(f"[check_llm_providers_health] {checked}", flush=True)
    return {"status": "ok", "healthy": checked}

