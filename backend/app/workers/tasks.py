"""ARQ 异步任务定义。

任务类型（对齐设计文档 §4.4 ETL 管线）：
- ETL 编排：crawl_platform / run_etl_pipeline / validate_temporal / detect_inflation / snapshot_graph
- 业务异步：resume_parse / batch_extract

设计要点：
- 爬虫通过 subprocess 调用 `scrapy crawl`，避免 Twisted reactor 与 asyncio loop 冲突
- ETL 任务编排采用 fail-fast：任一阶段失败立即抛出，由 ARQ 重试机制兜底
- 时滞/通胀检测 M2 仅交付框架，M3 LLM 抽取上线后接入真实数据
"""

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core import runtime_config
from app.services.alerting import send_alert
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
from app.workers.utils import (
    push_crawl_log as _push_crawl_log,  # noqa: F401  # legacy monkeypatch path
    update_crawl_task as _update_crawl_task,  # noqa: F401  # legacy monkeypatch path
)

from sqlalchemy import select
from app.models.raw import CommunityRaw, CourseRaw, JDRaw, PaperRaw


async def graph_health_check(ctx: dict) -> dict:
    """图谱健康巡检（08-15 全流程评估 P1）：每日 ETL 尾部自动检查。

    把人工图谱扫描自动化——超限项 → webhook 告警（复用 _alert_llm 去重）：
    1. 空权 REQUIRES 边（应为 0——#216 重复残留同源问题复发检测）
    2. 孤立 Position（无任何关系——无名/僵尸节点）
    3. candidate 状态岗位数（发现候选镜像，正常≈0）
    4. 孤立 Course 覆盖率（>80% 提示课程标签链路异常；当前 ~70% 为
       icourse163/edx 无标签数据源特性，非故障）
    """
    from app.core.database import neo4j_driver

    def _query() -> dict:
        with neo4j_driver.session() as s:
            return {
                "null_weight_edges": s.run(
                    "MATCH ()-[r:REQUIRES]->(:Skill) "
                    "WHERE r.weight IS NULL OR r.source_count IS NULL "
                    "RETURN count(r) AS n"
                ).single()["n"],
                "isolated_positions": s.run(
                    "MATCH (p:Position) WHERE NOT EXISTS { (p)--() } "
                    "RETURN count(p) AS n"
                ).single()["n"],
                "candidate_positions": s.run(
                    "MATCH (p:Position {status:'candidate'}) RETURN count(p) AS n"
                ).single()["n"],
                "total_courses": s.run("MATCH (c:Course) RETURN count(c) AS n").single()["n"],
                "isolated_courses": s.run(
                    "MATCH (c:Course) WHERE NOT EXISTS { (c)-[:LEARNABLE_VIA]-() } "
                    "RETURN count(c) AS n"
                ).single()["n"],
            }

    stats = await asyncio.to_thread(_query)
    alerts: list[tuple[str, str]] = []
    if stats["null_weight_edges"] > 0:
        alerts.append((
            "graph_null_weight_edges",
            f"空权 REQUIRES 边 {stats['null_weight_edges']} 条（应为 0——重复残留或新写入口径漂移）",
        ))
    if stats["isolated_positions"] > 0:
        alerts.append((
            "graph_isolated_positions",
            f"孤立 Position {stats['isolated_positions']} 个（无名/僵尸节点残留）",
        ))
    if stats["candidate_positions"] > 0:
        alerts.append((
            "graph_candidate_positions",
            f"图谱 candidate 岗位 {stats['candidate_positions']} 个（发现候选镜像，正常≈0）",
        ))
    course_rate = stats["isolated_courses"] / stats["total_courses"] if stats["total_courses"] else 0.0
    if course_rate > 0.8:
        alerts.append((
            "graph_course_coverage",
            f"孤立课程覆盖率 {course_rate:.0%}（>80%，课程标签链路异常；数据源特性基线 ~70%）",
        ))
    alerted = {}
    for event, msg in alerts:
        alerted[event] = await _alert_llm(event, msg)
    return {"stats": stats, "alerts": alerted}


async def diversity_report(ctx: dict, top_n: int = 10) -> dict:
    """数据多样性报告（DA-M4-02）。

    聚合四类 raw 表多样性指标，写入 reports/diversity_{date}.json（幂等覆盖）。
    指标口径见 app/services/data_quality/diversity.py。
    """

    from app.core.database import async_session_factory
    from app.services.data_quality.diversity import (
        course_diversity,
        dedup_stats,
        position_diversity,
        source_distribution,
    )

    async def _jd_items(rows):
        items = []
        for r in rows:
            ext = (r.snapshot or {}).get("extraction") or {}
            name = (ext.get("position_name") or "").strip()
            if not name:
                continue
            skills = [s.get("name") for s in (ext.get("skills") or []) if s.get("name")]
            items.append({"position_name": name, "skills": skills})
        return items

    async def _course_items(rows):
        items = []
        for r in rows:
            snap = r.snapshot or {}
            skills = snap.get("skills") or []
            if isinstance(skills, str):
                skills = [s.strip() for s in skills.split(",") if s.strip()]
            items.append({"platform": snap.get("platform", r.source), "skills": skills})
        return items

    async with async_session_factory() as session:
        jd_rows = (await session.scalars(select(JDRaw))).all()
        course_rows = (await session.scalars(select(CourseRaw))).all()
        paper_rows = (await session.scalars(select(PaperRaw))).all()
        community_rows = (await session.scalars(select(CommunityRaw))).all()

    report = {
        "generated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "jd": {
            "total": len(jd_rows),
            "sources": source_distribution([{"source": r.source} for r in jd_rows]),
            "dedup": dedup_stats([{"fingerprint": r.fingerprint} for r in jd_rows]),
            "positions": position_diversity(await _jd_items(jd_rows), top_n=top_n),
        },
        "course": {
            **course_diversity(await _course_items(course_rows)),
            "dedup": dedup_stats([{"fingerprint": r.fingerprint} for r in course_rows]),
        },
        "paper": {
            "total": len(paper_rows),
            "sources": source_distribution([{"source": r.source} for r in paper_rows]),
        },
        "community": {
            "total": len(community_rows),
            "sources": source_distribution([{"source": r.source} for r in community_rows]),
        },
    }

    report_dir = Path(__file__).resolve().parents[2] / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"diversity_{datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d')}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[diversity_report] 报告已写入: {report_path}", flush=True)
    return {"report_path": str(report_path)}


async def check_data_freshness(ctx: dict) -> dict:
    """数据更新新鲜度检查（DA-M4-03，设计文档 T+1 承诺）。

    按来源聚合四类 raw 表最新抓取时间，判定平台级新鲜度（≤1 天），
    写入 reports/freshness_{date}.json。过期来源返回在结果中供告警。
    """

    from app.core.database import async_session_factory
    from app.services.data_quality.update_status import platform_freshness

    async def _rows(model):
        async with async_session_factory() as session:
            return (await session.scalars(select(model))).all()

    def _section(rows):
        return platform_freshness(
            [{"source": r.source, "crawled_at": r.crawled_at} for r in rows]
        )

    report = {
        "generated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "jd": _section(await _rows(JDRaw)),
        "course": _section(await _rows(CourseRaw)),
        "paper": _section(await _rows(PaperRaw)),
        "community": _section(await _rows(CommunityRaw)),
    }

    report_dir = Path(__file__).resolve().parents[2] / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"freshness_{datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d')}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    stale = [
        f"{name}:{source}"
        for name in ("jd", "course", "paper", "community")
        for source in report[name]["stale_sources"]
    ]
    if stale:
        # T+1 承诺被破坏时告警，避免数据过期无人感知（§4.4 / DA-M4-03）
        await send_alert(
            "data_stale",
            f"数据过期来源（超过 T+1）: {', '.join(stale)}",
            stale_sources=stale,
            report_path=str(report_path),
        )
    print(f"[check_data_freshness] 报告已写入: {report_path} 过期来源: {stale}", flush=True)
    return {"report_path": str(report_path), "stale_sources": stale}


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


async def on_startup(ctx: dict) -> None:
    """Worker 启动钩子。

    预热 OCR 引擎（PaddleOCR 懒加载首次调用约 24s，2026-08-09 扫描件 OCR
    速度评测）：异步预加载到全局单例，使首次 resume_parse 免于 24s 冷加载。
    模型不可用（未下载/依赖缺失）时预热失败不阻塞 worker 启动，后续
    resume_parse 仍会按需懒加载并抛 ResumeParseError 由任务层处理。
    """
    print(f"[ARQ Worker] 启动，PID={ctx.get('worker_pid')}")

    async def _warm_ocr():
        try:
            from app.services.resume import file_parser as _fp

            _fp._ocr_engine()
            print("[ARQ Worker] OCR 引擎预热完成")
        except Exception as e:
            print(f"[ARQ Worker] OCR 预热跳过（模型不可用）: {str(e)[:100]}")

    asyncio.create_task(_warm_ocr())


async def on_shutdown(ctx: dict) -> None:
    """Worker 关闭钩子。"""
    print("[ARQ Worker] 关闭")

