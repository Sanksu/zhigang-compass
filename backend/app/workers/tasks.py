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

from sqlalchemy import or_, select
from app.models.raw import CommunityRaw, CourseRaw, JDRaw, PaperRaw


# ============================================================
# ETL 阶段任务
# ============================================================


# 课程技能抽取（enrich_course_skills）失败重试配置（08-16 用户要求）：
# 单课程 LLM 抽取失败后延迟 _ENRICH_RETRY_DELAY_SECONDS 秒再次进入队列
# （下次 ETL 阶段 5.5 到期才重试，避免瞬时故障风暴下每次 ETL 全量重试）；
# 累计失败达 _ENRICH_MAX_FAILS 次后放弃（写 skills_enriched，防无限重试）。
_ENRICH_RETRY_DELAY_SECONDS = 3600   # 失败后延迟 1 小时重试
_ENRICH_MAX_FAILS = 3                # 累计失败 3 次放弃


async def enrich_course_skills(ctx: dict, limit: int | None = None) -> dict:
    """新采集课程技能标签补全（T-05，2026-08-15）。

    背景：icourse163/edx 爬虫不产出 skills 字段（edx 写死空、icourse163 页面
    无数据）→ 课程无 LEARNABLE_VIA 静态边（存量 974 门孤立课程，产品走
    learning_path 语义兜底无功能缺陷）。本任务**仅处理新采集课程**
    （crawled_at >= 最近 7 天，容错 ETL 失败重跑；存量孤立课程不动——
    T-05 验收），LLM 从标题+描述抽取技能，门控（canonical + 停用词/白名单，
    与 import_course 同口径，防 08-13 静态脏边问题）后写回
    snapshot["skills"]；load_courses 阶段随之建 LEARNABLE_VIA 边。

    LLM 不可用/解析失败静默降级（写 skills_enriched 标记防重复抽取，
    不阻塞 ETL，与 RAG 接地同语义）。
    """
    from datetime import date, timedelta

    from sqlalchemy import func, select

    from app.core.database import async_session_factory
    from app.services.extraction.course_skills import (
        extract_course_skills,
        filter_skill_tags,
    )
    from app.services.extraction.jd_extractor import JDExtractor

    # 新采集窗口：最近 7 天（含 ETL 失败重跑容错；更早课程即存量孤立课程不处理）
    since = (date.today() - timedelta(days=7)).isoformat()
    # 延迟重试（08-16 用户要求）：LLM 抽取失败的课程延迟配置时间后再次入队，
    # 避免每次 ETL 都立即重试全部失败课程（LLM 瞬时故障风暴）；累计失败
    # 达上限后放弃（写 skills_enriched，防无限重试）
    retry_delay = timedelta(seconds=_ENRICH_RETRY_DELAY_SECONDS)
    retry_cutoff = datetime.now(timezone(timedelta(hours=8))) - retry_delay

    async with async_session_factory() as session:
        stmt = (
            select(CourseRaw)
            .where(
                or_(
                    CourseRaw.snapshot["skills"].astext.is_(None),
                    func.jsonb_typeof(CourseRaw.snapshot["skills"]) != "array",
                    func.jsonb_array_length(CourseRaw.snapshot["skills"]) == 0,
                ),
                CourseRaw.snapshot["skills_enriched"].astext.is_(None),
                CourseRaw.crawled_at >= since,
                # 延迟中跳过：skills_retry_at 未到（或缺失）才入选
                or_(
                    CourseRaw.snapshot["skills_retry_at"].astext.is_(None),
                    CourseRaw.snapshot["skills_retry_at"].astext <= retry_cutoff.isoformat(),
                ),
            )
            .order_by(CourseRaw.id.asc())
        )
        if limit:
            stmt = stmt.limit(limit)
        rows = (await session.scalars(stmt)).all()

    llm = None
    try:
        llm = JDExtractor().llm
    except Exception:
        llm = None

    enriched = 0
    skipped_no_llm = 0
    failed = 0
    updates: dict[int, dict] = {}
    for row in rows:
        snap = dict(row.snapshot or {})
        # 爬虫原始标签（如 coursera 段落解析）先过门控；仍有缺失才走 LLM
        skills = filter_skill_tags(snap.get("skills") or [])
        llm_errored = False
        if not skills:
            if llm is None:
                skipped_no_llm += 1
            else:
                try:
                    skills = extract_course_skills(
                        llm, snap.get("title", ""), snap.get("description", "")
                    )
                except Exception:
                    failed += 1
                    llm_errored = True
                    # 失败计数 + 延迟重试时间戳（下次 ETL 到期才重入队）
                    fails = int(snap.get("skills_enrich_fails") or 0) + 1
                    snap["skills_enrich_fails"] = fails
                    if fails >= _ENRICH_MAX_FAILS:
                        # 累计失败达上限：放弃（防无限重试），保留失败计数供排查
                        snap["skills_enriched"] = True
                    else:
                        snap["skills_retry_at"] = (
                            datetime.now(timezone(timedelta(hours=8))) + retry_delay
                        ).isoformat()
        if skills:
            snap["skills"] = skills
            enriched += 1
            # 标记已处理，防每次 ETL 对同一课程重复调用 LLM
            snap["skills_enriched"] = True
        elif llm is None:
            # LLM 不可用（skipped_no_llm）：不写标记——配置恢复后自动重试，
            # 避免"LLM 缺失期间误标已处理"导致课程永久无标签
            continue
        elif llm_errored:
            # 异常失败（未达放弃上限）：不写标记——retry_at 到期后重入队
            pass
        else:
            # LLM 正常判定无技能（宁少勿滥空数组）：标记防重复调用
            snap["skills_enriched"] = True
        updates[row.id] = snap
    if updates:
        # 08-15 修复：此前在已关闭的 session 的 ORM 对象上改 snapshot 后于新
        # session commit——detached 对象的修改不会落库，写回全部静默丢失
        # （实测 PG 0 条）。重新加载本 session 的 ORM 对象再写回。
        async with async_session_factory() as session:
            objs = (
                await session.scalars(
                    select(CourseRaw).where(CourseRaw.id.in_(list(updates)))
                )
            ).all()
            for o in objs:
                o.snapshot = updates[o.id]
            await session.commit()

    return {
        "checked": len(rows),
        "enriched": enriched,
        "skipped_no_llm": skipped_no_llm,
        "failed": failed,
    }


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


async def load_courses(ctx: dict) -> dict:
    """课程数据入图（course_raw → Course/Skill 节点 + LEARNABLE_VIA 关系）。

    遍历 course_raw.snapshot 调 import_course（Neo4j MERGE 幂等，重复执行
    不产生重复节点）。单条失败不阻塞整体（批量语义）。
    """

    from app.core.database import async_session_factory, neo4j_driver
    from app.services.kg.kg_service import import_course

    async with async_session_factory() as session:
        rows = (await session.scalars(select(CourseRaw))).all()
    data = [dict(r.snapshot or {}) for r in rows]

    def _import_all():
        # 同步 Neo4j 写入放线程池，避免阻塞 ARQ 事件循环（Redis 心跳超时崩溃根因）
        imported = 0
        failed = 0
        with neo4j_driver.session() as neo4j_session:
            for course_data in data:
                try:
                    import_course(neo4j_session, course_data)
                    imported += 1
                except Exception:
                    failed += 1
        return imported, failed

    imported, failed = await asyncio.to_thread(_import_all)
    return {"total": len(data), "imported": imported, "failed": failed}


async def evaluate_courses(ctx: dict) -> dict:
    """课程质量评估（DA-M4-01，设计文档 §4.6）。

    遍历 course_raw 全量课程 → 六维加权质量评分 → 幂等写回
    `snapshot["quality"]`（覆盖更新）。返回推荐池统计，供学习路径取 Top-3。
    """

    from app.core.database import async_session_factory
    from app.services.data_quality.course_quality import (
        RECOMMEND_MIN_SCORE,
        evaluate_course,
    )

    async with async_session_factory() as session:
        rows = (await session.scalars(select(CourseRaw).order_by(CourseRaw.id.asc()))).all()
        results = []
        for row in rows:
            snap = dict(row.snapshot or {})
            result = evaluate_course(snap)
            snap["quality"] = result.model_dump()
            row.snapshot = snap
            results.append(result)
        await session.commit()

    recommended = [r for r in results if r.recommended]
    return {
        "total": len(results),
        "recommended": len(recommended),
        "recommend_min_score": RECOMMEND_MIN_SCORE,
        "top3": [r.model_dump() for r in sorted(results, key=lambda r: r.quality_score, reverse=True)[:3]],
    }


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


async def snapshot_graph(ctx: dict, triggered_by: str = "scheduled") -> dict:
    """每日图谱版本快照（设计文档 §7.1 T+1 版本管理）。

    流程：Neo4j 全量导出 {nodes, edges}（排除 Counter 内部标签）→
    写入 PostgreSQL graph_versions（幂等：同日期版本覆盖更新）→
    与上一版本 set 差集计算节点增减 → 90 天保留清理。

    由外部 cron（scripts/cron/snapshot_daily.py）每日 05:00 前触发，
    或作为 run_etl_pipeline 阶段 12 随 ETL 完成后自动发布。
    """
    from app.services.evolution.graph_version import GraphVersionManager

    meta = await GraphVersionManager().create_snapshot(triggered_by=triggered_by)
    return meta.model_dump()




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

