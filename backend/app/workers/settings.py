"""ARQ worker registration and lifecycle settings."""

import asyncio
import logging

from arq.connections import RedisSettings
from arq.cron import cron
from arq.worker import func

from app.core import runtime_config
from app.core.config import settings
from app.workers.tasks import (
    aggregate_positions,
    backfill_embeddings,
    batch_extract,
    check_data_freshness,
    check_llm_providers_health,
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
    generate_diagnosis,
    graph_health_check,
    llm_stats_daily,
    name_normalization_shadow_daily,
    name_normalization_propose_daily,
    skill_relation_propose_daily,
    skill_category_review_daily,
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


logger = logging.getLogger(__name__)

# fire-and-forget 预热任务引用（第八轮 P2-18）：asyncio 官方文档——
# create_task 返回的 Task 若无强引用，事件循环只保留弱引用，任务可能在
# 执行途中被 GC 静默取消。保存引用 + 完成回调自清理；异常任务补 warning
# 日志（任务体内部已各自捕获，此处兜底防"静默死"无迹可查）。
_background_tasks: set[asyncio.Task] = set()


def _spawn_background(coro) -> None:
    """启动 fire-and-forget 预热任务：保存引用防 GC，完成回调移除并记录异常。"""

    def _done(task: asyncio.Task) -> None:
        _background_tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.warning("预热任务异常退出: %s", task.exception())

    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_done)


async def _sweep_orphan_running_tasks() -> int:
    """启动清扫：把 task_status 中残留 running 的僵尸行置 failed，返回行数。

    僵尸产生机制：任务成功/失败状态写在任务函数收尾路径内，worker 容器被重启/
    杀死时函数体无机会执行，DB 行永久冻结在 running（ARQ 对被杀进程中的 job 不
    重新入队，故既不重跑也不更新状态）。

    直扫全表无误伤的论证：running 状态只在 worker 进程内写入（API 侧只建
    pending）；本部署仅单 worker 容器，且 ARQ 先 await on_startup 再开始轮询
    队列——清扫完成时不可能存在合法的 running 行。
    """
    from sqlalchemy import update

    from app.core.database import async_session_factory
    from app.models.business import TaskStatus

    async with async_session_factory() as session:
        result = await session.execute(
            update(TaskStatus)
            .where(TaskStatus.status == "running")
            .values(status="failed", error="worker 重启中断（启动清扫）")
        )
        await session.commit()
        return result.rowcount


async def on_startup(ctx: dict) -> None:
    """Preload the OCR engine without blocking worker startup."""
    logger.info("ARQ worker 启动，PID=%s", ctx.get("worker_pid"))

    # 启动清扫（僵尸态收尸）：见 _sweep_orphan_running_tasks。清扫必须先于
    # 队列轮询完成，故直接 await 而非 _spawn_background；DB 瞬断不阻塞 worker
    # 启动（错误落日志，容器重启策略触发下次启动重试）。
    try:
        swept = await _sweep_orphan_running_tasks()
        if swept:
            logger.warning("启动清扫：%d 条僵尸任务行（running→failed）", swept)
    except Exception as error:
        logger.error("启动清扫失败（worker 继续启动）: %s", str(error)[:200])

    async def warm_ocr() -> None:
        try:
            from app.services.resume import file_parser

            file_parser._ocr_engine()
            logger.info("OCR 引擎预热完成")
        except Exception as error:
            logger.warning("OCR 预热跳过（模型不可用）: %s", str(error)[:100])

    _spawn_background(warm_ocr())

    async def warm_dynamic_aliases() -> None:
        # 动态别名表（方案①）加载：worker 进程是 normalize_skill 的主消费方
        # （ETL 抽取链）；approve 只刷新 API 进程内存，worker 靠此处 + 每轮
        # ETL 起点刷新兜底（第六轮审查 P0-1 跨进程生效口径）。内部 fail-soft。
        from app.services.extraction.dictionary import refresh_dynamic_aliases

        loaded = await refresh_dynamic_aliases()
        logger.info("动态别名表 worker 启动加载完成：%d 条", loaded)

    _spawn_background(warm_dynamic_aliases())

    async def warm_jd_pool() -> None:
        # JD 池化向量预热（方案 A：匹配主链路恒走 JD 候选模式，池化向量为
        # 向量预筛召回必备——启动即构建池化并写 Redis，免容器重建后首个匹配
        # 请求承担冷启动；指纹命中则秒级返回）。失败不阻塞 worker。
        try:
            from sqlalchemy import select

            from app.core.database import async_session_factory, redis_client
            from app.models.raw import JDRaw
            from app.services.matching.jd_profiles import rows_to_profiles
            from app.services.matching.jd_vector_recall import load_pool_vectors_cached
            from app.services.matching.semantic import SkillEmbedder

            async with async_session_factory() as session:
                rows = (await session.scalars(
                    select(JDRaw)
                    .where(JDRaw.snapshot["extraction"].astext.is_not(None))
                    .order_by(JDRaw.id)
                )).all()
            profiles, _ = rows_to_profiles(rows)
            vecs = await load_pool_vectors_cached(
                profiles, SkillEmbedder.get(), redis_client,
            )
            logger.info(
                "JD 池化向量预热完成：%d 条 JD（SBERT 不可用已降级跳过：%s）",
                len(profiles), vecs is None,
            )
        except Exception as error:
            logger.warning("JD 池化预热跳过: %s", str(error)[:100])

    _spawn_background(warm_jd_pool())


async def on_shutdown(ctx: dict) -> None:
    """Log worker shutdown."""
    logger.info("ARQ worker 关闭")


def _crawler_cron_jobs() -> list:
    """每爬虫独立触发时间的 cron 注册（08-21b）。

    arq cron 无法给任务传参，故注册**单个每分钟 cron** 调用 crawl_scheduler；
    crawl_scheduler 内部按"当前 HH:MM == 配置 hour/minute"匹配到点的爬虫并触发。
    未配置独立时间的爬虫由 ETL 主管线统一触发（crawl_scheduler 内跳过，防双跑）。
    worker 重启后生效（与其余运行时配置一致）。
    """
    return [
        cron(
            crawl_scheduler,
            minute=set(range(0, 60)),
            run_at_startup=False,
        )
    ]


class WorkerSettings:
    """ARQ worker configuration.

    Start with ``arq app.workers.settings.WorkerSettings``.
    """

    functions = [
        # crawl_platform 显式放宽超时（H2 修复）：全局 job_timeout=1800s 会
        # 在 zhilian 独立触发合法采集（最长 7200s）完成前 kill 掉 ARQ 任务。
        # 7200s 对齐 _CRAWL_TIMEOUT_BY_SPIDER["zhilian"]；其余源内部 900s 兜底。
        func(crawl_platform, timeout=7200, max_tries=1),
        func(crawl_scheduler, timeout=7200, max_tries=1),
        func(run_etl_pipeline, timeout=10800, max_tries=1),
        func(run_etl_pipeline_scheduled, timeout=10800, max_tries=1),
        # 管理端手动触发包装（快捷操作面板）：完整管线最长窗口对齐主管线
        func(run_etl_job_manual, timeout=10800, max_tries=1),
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
        llm_stats_daily,
        name_normalization_shadow_daily,
        name_normalization_propose_daily,
        skill_relation_propose_daily,
        skill_category_review_daily,
        check_llm_providers_health,
        graph_health_check,
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(settings.arq_redis_url)
    concurrency = runtime_config.get("arq_concurrency", settings.arq_concurrency)
    job_timeout = runtime_config.get("arq_job_timeout", settings.arq_job_timeout)
    max_retries = 2
    retry_delay = 10
    cron_jobs = [
        cron(
            check_llm_providers_health,
            minute=set(range(0, 60, 5)),
            run_at_startup=True,
        ),
        cron(watch_signal_daily, hour=6, minute=0),
        # ETL 主管线（08-21 容器内调度，替代外部计划任务 etl_daily.py）：
        # 执行时间来自配置中心 runtime_config.etl_run_hour/minute，重启后生效；
        # 当日幂等由 run_etl_pipeline_scheduled 内部 Redis 锁保证。
        cron(
            run_etl_pipeline_scheduled,
            hour=runtime_config.get("etl_run_hour", 5),
            minute=runtime_config.get("etl_run_minute", 0),
            run_at_startup=False,
        ),
        # 每爬虫独立 cron（08-21b）：仅配置了 hour+minute 的启用爬虫注册；
        # 未配置独立时间的爬虫由 ETL 主管线统一触发（防双跑）。
        *_crawler_cron_jobs(),
    ]
