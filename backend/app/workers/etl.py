"""ETL orchestration helpers and the complete pipeline ARQ task."""

import asyncio
from datetime import datetime, timedelta, timezone

from app.core import runtime_config
from app.models.raw import JDRaw

_ETL_LIMIT_CAP = 2000  # 默认批次上限（可经 runtime_config.etl_batch_cap 覆盖）

# 同 run_date 幂等锁 TTL：覆盖 ETL 最长执行窗口（per-function timeout 3h），
# 防止 cron 重触发/手动触发在当日重复入图（与 scripts/cron/etl_daily.py 语义一致）
_ETL_RUN_LOCK_TTL = 60 * 60 * 24


async def _etl_run_lock_acquire(run_date: str) -> bool:
    """当日 ETL 幂等锁（Redis SET NX，24h TTL）。返回 True=首次获得可执行。"""
    from arq import create_pool
    from arq.connections import RedisSettings

    from app.core.config import settings

    client = await create_pool(RedisSettings.from_dsn(settings.arq_redis_url))
    try:
        acquired = await client.set(
            f"arq:etl:run:{run_date}", "1", nx=True, ex=_ETL_RUN_LOCK_TTL
        )
        return bool(acquired)
    finally:
        await client.close()


async def _etl_limit(extracted: bool, default: int) -> int:
    """Scale the ETL batch limit to the pending backlog (capped by config)."""
    from sqlalchemy import func, select

    from app.core.database import async_session_factory

    if extracted:
        predicate = JDRaw.snapshot["extraction"].astext.is_(None)
    else:
        predicate = (JDRaw.snapshot["extraction"].astext.isnot(None)) & (
            JDRaw.snapshot["validation"].astext.is_(None)
        )
    async with async_session_factory() as session:
        pending = await session.scalar(
            select(func.count()).select_from(JDRaw).where(predicate)
        ) or 0
    cap = runtime_config.get("etl_batch_cap", _ETL_LIMIT_CAP)
    return min(max(pending, default), cap)


async def _run_stage(name: str, coro) -> dict:
    """Run one idempotent ETL stage without blocking subsequent stages."""
    try:
        return await coro
    except Exception as error:
        return {"error": f"{type(error).__name__}: {str(error)[:300]}"}


async def _run_limited_stage(
    name: str,
    *,
    extracted: bool,
    default: int,
    task,
    ctx: dict,
    task_kwargs: dict | None = None,
    limit_getter=None,
) -> dict:
    """Include backlog-limit lookup in the stage isolation boundary."""
    try:
        get_limit = limit_getter or _etl_limit
        limit = await get_limit(extracted, default)
        return await task(ctx, limit=limit, **(task_kwargs or {}))
    except Exception as error:
        return {"error": f"{type(error).__name__}: {str(error)[:300]}"}


async def run_etl_pipeline(
    ctx: dict,
    run_date: str | None = None,
    skip_cdp: bool = False,
    *,
    tasks_module=None,
) -> dict:
    """Run the complete ETL pipeline in dependency order.

    ``tasks_module`` is an internal compatibility seam. The public wrapper in
    ``app.workers.tasks`` supplies that module so existing monkeypatches of task
    dependencies continue to affect orchestration.
    """
    if tasks_module is None:
        from app.workers import tasks as tasks_module

    if run_date is None:
        run_date = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

    domestic_platforms = ["zhilian"]
    international_platforms = ["indeed", "glassdoor"]
    trend_platforms = ["arxiv", "github", "stackoverflow"]
    crawl_platforms = domestic_platforms + international_platforms + trend_platforms
    if skip_cdp:
        crawl_platforms = [
            platform
            for platform in crawl_platforms
            if platform not in tasks_module.CDP_SPIDERS
        ]

    # 每爬虫采集配置（08-21）：enabled=false 停用该源；max_results 传 crawler 层
    # （仅 arxiv/zhilian 显式消费，其余源忽略——crawl.py MAX_RESULTS_SUPPORTED）
    crawler_cfg = runtime_config.get("crawlers") or {}

    results: dict = {"run_date": run_date, "stages": {}}

    crawl_results = []
    for spider in crawl_platforms:
        cfg = crawler_cfg.get(spider) or {}
        if cfg.get("enabled") is False:
            crawl_results.append({"spider": spider, "skipped": "disabled_by_config"})
            continue
        try:
            crawl_results.append(
                await tasks_module.crawl_platform(
                    ctx, spider, max_results=cfg.get("max_results")
                )
            )
        except Exception as error:
            crawl_results.append({"spider": spider, "error": str(error)})
    results["stages"]["crawl"] = crawl_results
    results["stages"]["clean_dedup"] = {
        "status": "embedded_in_scrapy_pipeline"
    }

    run_stage = tasks_module._run_stage
    run_limited_stage = tasks_module._run_limited_stage
    structure_default = runtime_config.get("etl_structure_load_default", 500)
    validate_default = runtime_config.get("etl_validate_temporal_default", 200)
    results["stages"]["dedup_simhash"] = await run_stage(
        "dedup_simhash",
        tasks_module.dedup_simhash(ctx),
    )
    results["stages"]["structure_load"] = await run_limited_stage(
        "structure_load",
        extracted=True,
        default=structure_default,
        task=tasks_module.batch_extract,
        ctx=ctx,
    )
    results["stages"]["validate_temporal"] = await run_limited_stage(
        "validate_temporal",
        extracted=False,
        default=validate_default,
        task=tasks_module.validate_temporal,
        ctx=ctx,
        task_kwargs={"jd_ids": []},
    )
    results["stages"]["detect_inflation"] = await run_limited_stage(
        "detect_inflation",
        extracted=False,
        default=validate_default,
        task=tasks_module.detect_inflation,
        ctx=ctx,
        task_kwargs={"jd_ids": []},
    )

    simple_stages = (
        ("enrich_course_skills", tasks_module.enrich_course_skills),
        ("load_courses", tasks_module.load_courses),
        ("evaluate_courses", tasks_module.evaluate_courses),
        ("aggregate_positions", tasks_module.aggregate_positions),
        ("cross_validate", tasks_module.cross_validate_jds),
        ("skill_normalization", tasks_module.sync_skill_normalization),
        ("diversity_report", tasks_module.diversity_report),
        ("check_data_freshness", tasks_module.check_data_freshness),
    )
    for stage_name, stage_task in simple_stages:
        results["stages"][stage_name] = await run_stage(
            stage_name,
            stage_task(ctx),
        )

    from app.core.database import neo4j_driver
    from app.services.kg.skill_relations import sync_skill_relations

    def _run_skill_relations() -> dict:
        with neo4j_driver.session() as session:
            return sync_skill_relations(session)

    results["stages"]["skill_relations"] = await run_stage(
        "skill_relations",
        asyncio.to_thread(_run_skill_relations),
    )

    from app.services.evolution.evolved_from import derive_evolved_from

    results["stages"]["evolved_from"] = await run_stage(
        "evolved_from",
        derive_evolved_from(),
    )
    results["stages"]["backfill_embeddings"] = await run_stage(
        "backfill_embeddings",
        tasks_module.backfill_embeddings(ctx),
    )
    results["stages"]["snapshot_graph"] = await run_stage(
        "snapshot_graph",
        tasks_module.snapshot_graph(ctx, triggered_by="scheduled"),
    )

    # 阶段 14.5：岗位画像共享缓存预构建（P1）——聚合与快照完成后重建
    # 版本化载荷并切指针（先写载荷后切指针，旧版本保持可读；失败仅记审计，
    # 不阻塞 ETL——匹配侧按指针读取或走降级路径）
    from app.services.matching.shared_cache import load_positions_shared

    results["stages"]["positions_cache_prebuild"] = await run_stage(
        "positions_cache_prebuild",
        load_positions_shared(),
    )
    results["stages"]["discovery_daily"] = await run_stage(
        "discovery_daily",
        tasks_module.discovery_daily(ctx),
    )
    results["stages"]["discovery_auto_transition"] = await run_stage(
        "discovery_auto_transition",
        tasks_module.discovery_auto_transition(ctx),
    )
    results["stages"]["graph_health_check"] = await run_stage(
        "graph_health_check",
        tasks_module.graph_health_check(ctx),
    )
    return results


async def run_etl_pipeline_scheduled(ctx: dict, *, tasks_module=None) -> dict:
    """容器内 ARQ cron 调度入口（08-21）：当日幂等 + 转发主管线。

    替代外部计划任务调用 scripts/cron/etl_daily.py 的调度方式：ETL 执行时间
    由配置中心（runtime_config.etl_run_hour/minute）控制，worker 重启后生效。
    锁语义与 etl_daily.enqueue_etl_pipeline 一致——同 run_date 仅允许执行一次，
    锁命中（当日已跑/正在跑）直接跳过返回。
    """
    if tasks_module is None:
        from app.workers import tasks as tasks_module

    run_date = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    if not await _etl_run_lock_acquire(run_date):
        return {
            "run_date": run_date,
            "skipped": "duplicate_day_lock",
            "msg": "当日 ETL 已在队列/执行中，跳过重复触发",
        }
    return await tasks_module.run_etl_pipeline(ctx, run_date=run_date)
