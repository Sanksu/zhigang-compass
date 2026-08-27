"""ETL orchestration helpers and the complete pipeline ARQ task."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.core import runtime_config
from app.models.raw import JDRaw

logger = logging.getLogger(__name__)

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


# 事实阶段（08-23 全流程闭环审查 P0）：去重/LLM 抽取入图/岗位聚合构成图谱
# 事实链——任一失败意味着当日图谱不完整。此时发布新快照会让演化 Z-score
# 序列和岗位发现消费一个"部分失败被当作新事实"的版本，因此下游派生阶段
# 整体跳过（治理/报告阶段不依赖当日数据完整性，继续执行）。
_FACT_STAGES = ("dedup_simhash", "structure_load", "aggregate_positions")

# 受事实门禁跳过的派生阶段：快照发布/演化推导/画像缓存/新岗位发现。
_FACT_GATED_STAGES = (
    "backfill_embeddings",
    "snapshot_graph",
    "evolved_from",
    "positions_cache_prebuild",
    "discovery_daily",
    "discovery_auto_transition",
)


def _stage_failed(result) -> bool:
    """阶段结果是否为硬失败（_run_stage/_run_limited_stage 捕获的 error 项）。"""
    return isinstance(result, dict) and isinstance(result.get("error"), str)


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

    Fact stages (dedup/extract/aggregate) gate the derived stages: any hard
    failure marks the run ``pipeline_status=degraded`` and skips snapshot /
    evolved_from / discovery so partial data is never published as a new
    graph version. Governance stages (health/dict-guard/stats) still run.

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
    # 08-21b 修复（H1 防双跑）：已配置独立 hour/minute 的爬虫由 crawl_scheduler
    # 单独触发，主管线跳过——否则每日主管线 + 独立 cron 会重复爬取同一源。
    crawler_cfg = runtime_config.get("crawlers") or {}

    results: dict = {"run_date": run_date, "stages": {}}

    # 动态别名表按轮刷新（方案① 跨进程生效口径，第六轮审查 P0-1）：approve
    # 只即时刷新 API 进程；worker 进程在此处（每轮 ETL 起点，先于抽取归一）
    # 拉齐 skill_aliases approved 行。内部 fail-soft+warning，不阻断管线。
    from app.services.extraction.dictionary import refresh_dynamic_aliases

    await refresh_dynamic_aliases()

    crawl_results = []
    for spider in crawl_platforms:
        cfg = crawler_cfg.get(spider) or {}
        if cfg.get("enabled") is False:
            crawl_results.append({"spider": spider, "skipped": "disabled_by_config"})
            continue
        if "hour" in cfg and "minute" in cfg:
            crawl_results.append(
                {"spider": spider, "skipped": "independent_schedule"}
            )
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

    # 事实门禁：事实链任一失败 → 快照/演化/发现整体跳过（记 skipped 审计项），
    # 防止不完整数据被当作新事实版本发布；治理/报告阶段不受影响继续执行。
    fact_failures = [
        name for name in _FACT_STAGES if _stage_failed(results["stages"].get(name))
    ]
    pipeline_status = "degraded" if fact_failures else "complete"
    results["pipeline_status"] = pipeline_status

    if pipeline_status == "degraded":
        for stage_name in _FACT_GATED_STAGES:
            results["stages"][stage_name] = {
                "skipped": "pipeline_degraded",
                "failed_fact_stages": fact_failures,
            }
    else:
        from app.services.evolution.evolved_from import derive_evolved_from

        # 顺序：先回填向量、发布当日快照，演化关系推导基于「上一版本+当日本
        # 版本」配对——旧顺序在快照前推导，当日新数据要滞后一轮才参与演化。
        results["stages"]["backfill_embeddings"] = await run_stage(
            "backfill_embeddings",
            tasks_module.backfill_embeddings(ctx),
        )
        results["stages"]["snapshot_graph"] = await run_stage(
            "snapshot_graph",
            tasks_module.snapshot_graph(ctx, triggered_by="scheduled"),
        )
        results["stages"]["evolved_from"] = await run_stage(
            "evolved_from",
            derive_evolved_from(),
        )

        # 新岗位发现消费快照窗口，同样只在事实链完整时执行
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

    # 阶段 16：dict-guard 每日字典守卫（聚合/快照之后评估图谱形态；
    # 分级自动生效或进审核池，失败不阻塞管线——见 workers/dict_guard.py）
    results["stages"]["dict_guard"] = await run_stage(
        "dict_guard",
        tasks_module.dict_guard_daily(ctx),
    )

    # 阶段 17：LLM 调用统计日报（#454 审计计数聚合 → reports/llm_stats_{date}.json；
    # 报告 only 无阈值动作，失败不阻塞管线——见 workers/llm_stats.py）
    results["stages"]["llm_stats"] = await run_stage(
        "llm_stats",
        tasks_module.llm_stats_daily(ctx),
    )

    # 阶段 18：技能分类 LLM 审查（未分类技能灰度提议，默认关；
    # 只写 suggested_category 提议字段不动权威 category——见 workers/skill_category_review.py）
    results["stages"]["skill_category_review"] = await run_stage(
        "skill_category_review",
        tasks_module.skill_category_review_daily(ctx),
    )

    # 阶段 19：名称归一 LLM 影子审查（默认关；岗位名/技能名归一决策只落
    # llm_decision_records status=shadow 不生效——见 workers/name_normalization_shadow.py）
    results["stages"]["name_normalization_shadow"] = await run_stage(
        "name_normalization_shadow",
        tasks_module.name_normalization_shadow_daily(ctx),
    )

    # 阶段 20：名称归一 LLM 提议（默认关；proposal→人工审批→sync 落图，
    # 区别于阶段 19 shadow 只落档——见 workers/name_normalization_propose.py）
    results["stages"]["name_normalization_propose"] = await run_stage(
        "name_normalization_propose",
        tasks_module.name_normalization_propose_daily(ctx),
    )

    # 阶段 21：技能关系 LLM 提议（默认关；JD 共现候选 → proposal→人工审批→
    # sync 落图——见 workers/skill_relation_propose.py）
    results["stages"]["skill_relation_propose"] = await run_stage(
        "skill_relation_propose",
        tasks_module.skill_relation_propose_daily(ctx),
    )

    # L-9：阶段隔离吞错继续跑（防单阶段失败拖垮全线）不等于无声——聚合各阶段
    # error 一次性外发告警并落 error 日志，结束"管线永远成功"的可观测盲区。
    # crawl 阶段为 list[dict]（每爬虫一项），其余阶段为 dict。
    stage_errors: list[str] = []
    if pipeline_status == "degraded":
        stage_errors.append(
            f"pipeline_degraded: 快照/演化/发现已跳过（事实阶段失败: "
            f"{', '.join(fact_failures)}）"
        )
    for stage_name, stage_result in results["stages"].items():
        entries = (
            stage_result
            if isinstance(stage_result, list)
            else [stage_result]
            if isinstance(stage_result, dict)
            else []
        )
        for entry in entries:
            if isinstance(entry, dict) and isinstance(entry.get("error"), str):
                stage_errors.append(f"{stage_name}: {entry['error'][:120]}")
    if stage_errors:
        logger.error(
            "ETL 完成（pipeline_status=%s），但 %d 个阶段项失败：\n%s",
            pipeline_status,
            len(stage_errors),
            "\n".join(stage_errors),
        )
        from app.services.alerting import send_alert

        try:
            await send_alert(
                "etl_stage_errors",
                f"ETL 阶段失败聚合（{len(stage_errors)} 项）：\n" + "\n".join(stage_errors[:20]),
            )
        except Exception:
            logger.warning("ETL 阶段失败告警外发异常", exc_info=True)
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


# 管理端手动触发白名单（快捷操作面板，契约 /admin/etl/trigger）：
# job 标识 → tasks 门面上的函数名。与 api/v1/admin_routes/etl.py 的
# ETL_JOB_LABELS 同步维护（白名单双写点）。
_MANUAL_JOBS: dict[str, str] = {
    "dedup_simhash": "dedup_simhash",
    "aggregate_positions": "aggregate_positions",
    "run_etl_pipeline": "run_etl_pipeline",
}


def _jsonable_or_summary(result) -> dict:
    """阶段返回值 → JSONB 可存 dict（不可序列化时降级字符串摘要）。"""
    import json as _json

    if not isinstance(result, dict):
        return {"summary": str(result)}
    try:
        _json.dumps(result, ensure_ascii=False, default=str)
        return result
    except (TypeError, ValueError):
        return {"summary": str(result)[:2000]}


async def run_etl_job_manual(
    ctx: dict,
    job_name: str,
    task_id: str,
    *,
    tasks_module=None,
) -> dict:
    """管理端手动触发的 ETL 任务统一入口（快捷操作面板数据清洗/入图按钮）。

    白名单校验在 API 层完成；本包装维护 TaskStatus 生命周期
    pending→running→success/failed——管线/阶段函数为纯计算编排不追踪
    任务状态，与 crawl_platform 的 task_id 直写模式不同。错误详情仅入
    服务端日志，TaskStatus.error 固定文案（防经状态查询端点透传内部信息）。
    """
    import json

    if tasks_module is None:
        from app.workers import tasks as tasks_module
    from app.core.database import async_session_factory
    from app.models.business import TaskStatus

    target = _MANUAL_JOBS.get(job_name)
    fn = getattr(tasks_module, target, None) if target else None

    async with async_session_factory() as session:
        task = await session.get(TaskStatus, task_id)
        if task is None:
            logger.error("手动 ETL 任务缺少 TaskStatus 行: job=%s task_id=%s", job_name, task_id)
            return {"status": "failed", "error": "task 不存在"}
        if fn is None:
            task.status = "failed"
            task.error = "未知任务类型"
            await session.commit()
            return {"status": "failed", "error": "未知 job"}

        task.status = "running"
        task.progress = 0.05
        await session.commit()
        try:
            result = await fn(ctx)
        except Exception:
            logger.exception("手动 ETL 任务执行失败: job=%s task_id=%s", job_name, task_id)
            task.status = "failed"
            task.error = "任务执行失败"
            await session.commit()
            return {"status": "failed", "error": "任务执行失败"}

        task.status = "success"
        task.progress = 1.0
        payload = {"job": job_name, **_jsonable_or_summary(result)}
        json.dumps(payload, ensure_ascii=False, default=str)  # 写库前自证可序列化
        task.result = payload
        await session.commit()
        logger.info("手动 ETL 任务完成: job=%s task_id=%s", job_name, task_id)
        return {"status": "success"}
