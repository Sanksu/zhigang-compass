"""Data quality reports and graph health ARQ tasks.

Ownership: quality/ops tasks (diversity_report / check_data_freshness /
graph_health_check) run at the tail of the ETL pipeline and by cron.
graph_health_check reuses ``_alert_llm`` from the facade ``app.workers.tasks``
(lazy import to avoid a circular dependency). All names are re-exported from
``app.workers.tasks`` so WorkerSettings registration and existing
``from app.workers.tasks import ...`` imports keep working — function names
(``__qualname__``) must stay identical for ARQ job matching.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.services.alerting import send_alert
from app.models.raw import CommunityRaw, CourseRaw, JDRaw, PaperRaw

logger = logging.getLogger(__name__)


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
    # 延迟导入避免循环依赖：_alert_llm 定义于 tasks 门面（quality 被 tasks 导入）
    from app.workers.tasks import _alert_llm

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
    logger.info("diversity 报告已写入: %s", report_path)
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
    logger.info("数据新鲜度报告已写入: %s 过期来源: %s", report_path, stale)
    return {"report_path": str(report_path), "stale_sources": stale}
