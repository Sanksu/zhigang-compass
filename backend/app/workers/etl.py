"""ETL orchestration helpers and the complete pipeline ARQ task."""

import asyncio
from datetime import datetime, timedelta, timezone

from app.models.raw import JDRaw

_ETL_LIMIT_CAP = 2000


async def _etl_limit(extracted: bool, default: int) -> int:
    """Scale the ETL batch limit to the pending backlog."""
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
    return min(max(pending, default), _ETL_LIMIT_CAP)


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

    results: dict = {"run_date": run_date, "stages": {}}

    crawl_results = []
    for spider in crawl_platforms:
        try:
            crawl_results.append(await tasks_module.crawl_platform(ctx, spider))
        except Exception as error:
            crawl_results.append({"spider": spider, "error": str(error)})
    results["stages"]["crawl"] = crawl_results
    results["stages"]["clean_dedup"] = {
        "status": "embedded_in_scrapy_pipeline"
    }

    run_stage = tasks_module._run_stage
    run_limited_stage = tasks_module._run_limited_stage
    results["stages"]["dedup_simhash"] = await run_stage(
        "dedup_simhash",
        tasks_module.dedup_simhash(ctx),
    )
    results["stages"]["structure_load"] = await run_limited_stage(
        "structure_load",
        extracted=True,
        default=500,
        task=tasks_module.batch_extract,
        ctx=ctx,
    )
    results["stages"]["validate_temporal"] = await run_limited_stage(
        "validate_temporal",
        extracted=False,
        default=200,
        task=tasks_module.validate_temporal,
        ctx=ctx,
        task_kwargs={"jd_ids": []},
    )
    results["stages"]["detect_inflation"] = await run_limited_stage(
        "detect_inflation",
        extracted=False,
        default=200,
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
