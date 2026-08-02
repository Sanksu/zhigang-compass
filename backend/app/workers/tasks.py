"""ARQ 异步任务定义。

任务类型（对齐设计文档 §4.4 ETL 管线）：
- ETL 编排：crawl_platform / run_etl_pipeline / validate_temporal / detect_inflation / snapshot_graph
- 业务异步：resume_parse / batch_extract / evolution_compute

设计要点：
- 爬虫通过 subprocess 调用 `scrapy crawl`，避免 Twisted reactor 与 asyncio loop 冲突
- ETL 任务编排采用 fail-fast：任一阶段失败立即抛出，由 ARQ 重试机制兜底
- 时滞/通胀检测 M2 仅交付框架，M3 LLM 抽取上线后接入真实数据
"""

import asyncio
import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from arq.connections import RedisSettings

from app.core.config import settings

# ── 爬虫项目根（backend/data/crawlers）──
_CRAWLERS_DIR = Path(__file__).resolve().parents[2] / "data" / "crawlers"
_OUTPUT_DIR = _CRAWLERS_DIR / "output"

# 显式消费 -a max_results 参数的 spider（其余源由各自默认采集量控制）
MAX_RESULTS_SUPPORTED = {"arxiv"}


# ============================================================
# ETL 阶段任务
# ============================================================

async def crawl_platform(
    ctx: dict,
    spider_name: str,
    keywords: list[str] | None = None,
    cities: list[str] | None = None,
    max_results: int | None = None,
) -> dict:
    """触发单个 Scrapy 爬虫。

    通过 subprocess 调用而非 in-process，原因：
    - Scrapy 基于 Twisted reactor，与 asyncio event loop 不兼容
    - subprocess 隔离崩溃，单爬虫失败不污染 worker

    输出：output/{spider}_{YYYYMMDD_HHMMSS}.jsonl
    """
    timestamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")
    output_file = _OUTPUT_DIR / f"{spider_name}_{timestamp}.jsonl"
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "scrapy", "crawl", spider_name,
        "-o", str(output_file),
    ]
    if keywords:
        cmd.extend(["-a", f"keywords={','.join(keywords)}"])
    if cities:
        cmd.extend(["-a", f"cities={','.join(cities)}"])
    # max_results 仅 arxiv 等显式消费该参数的 spider 生效，其余忽略并提示（避免静默失效）
    if max_results:
        if spider_name in MAX_RESULTS_SUPPORTED:
            cmd.extend(["-a", f"max_results={max_results}"])
        else:
            print(f"[crawl_platform] spider={spider_name} 不支持 max_results，参数已忽略", flush=True)

    # cwd 设到 crawlers/ 让 scrapy.cfg 生效
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(_CRAWLERS_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"爬虫 {spider_name} 退出码 {proc.returncode}: "
            f"{stderr.decode('utf-8', errors='replace')[-2000:]}"
        )

    # 统计产出条数（按行数）
    line_count = 0
    if output_file.exists():
        with output_file.open(encoding="utf-8") as f:
            line_count = sum(1 for _ in f)

    return {
        "spider": spider_name,
        "output_file": str(output_file.relative_to(_CRAWLERS_DIR.parent.parent)),
        "items": line_count,
        "crawled_at": timestamp,
    }


# ============================================================
# 时滞 / 通胀检测辅助函数（设计文档 §4.7/4.8，M3 接入 jd_raw）
# ============================================================

# 与 extraction/schemas.py REQUIRESRelation.level 对齐的岗位级别集合
_QUALITY_LEVELS = {"初级", "中级", "高级", "资深", "专家"}


def _extraction_of(row) -> dict | None:
    """从 jd_raw 行取 LLM 抽取结果（snapshot.extraction），缺失返回 None。"""
    snap = row.snapshot or {}
    ext = snap.get("extraction")
    return ext if isinstance(ext, dict) else None


def _skills_of(ext: dict) -> list[str]:
    """抽取结果的技能名列表（requirements 优先，缺省 skills）。"""
    reqs = ext.get("requirements") or []
    if reqs:
        return [r.get("skill_name", "") for r in reqs if r.get("skill_name")]
    return [s.get("name", "") for s in (ext.get("skills") or []) if s.get("name")]


def _publish_date(snapshot: dict, crawled_at: str) -> date | None:
    """解析发布日期：snapshot.post_date 优先，缺省用 crawled_at；无法解析返回 None。"""
    raw = str(snapshot.get("post_date") or crawled_at or "")[:19]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _skill_first_seen_days(
    group: list[tuple[int, date, list[str]]],
    skills: list[str],
    today: date,
) -> list[int]:
    """技能首见时长（天）：同岗位 JD 中该技能最早出现日期到 today 的间隔。

    group: 同岗位已抽取记录 (jd_id, publish_date, skills)，含当前 JD。
    某技能在同岗位无任何记录时不计入（数据不足不武断判定）。
    """
    ages = []
    for skill in skills:
        first = None
        for _, pdate, group_skills in group:
            if skill in group_skills and (first is None or pdate < first):
                first = pdate
        if first is not None:
            ages.append(max(0, (today - first).days))
    return ages


def _experience_years(snapshot: dict) -> int | None:
    """解析经验要求最小年限（如 "3-5年" → 3）；无法解析返回 None。"""
    import re

    m = re.search(r"(\d+)", str(snapshot.get("experience") or ""))
    return int(m.group(1)) if m else None


async def validate_temporal(
    ctx: dict,
    jd_ids: list[int] | None = None,
    limit: int = 200,
) -> dict:
    """时滞检测（设计文档 §4.7）：jd_raw 已抽取记录接入 SAI/僵尸/抄袭检测。

    技能首见时长无图谱 `first_seen_at` 时，用同岗位 jd_raw 历史最早出现日期近似。
    检测结果写回 `snapshot["validation"]`（含三类结果 + 叠加降权系数）；
    数据不足（无技能/无发布日期）的 JD 跳过，不做武断判定。
    """
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.raw import JDRaw
    from app.services.data_quality.temporal_detector import (
        RECENT_WINDOW_DAYS,
        apply_temporal_decay,
        classify_sai,
        compute_sai,
        detect_plagiarism,
        detect_zombie_jd,
    )
    from app.services.data_quality.schemas import JDSkillSet

    today = date.today()
    async with async_session_factory() as session:
        stmt = select(JDRaw).where(JDRaw.snapshot["extraction"].astext.isnot(None))
        if jd_ids:
            stmt = stmt.where(JDRaw.id.in_(jd_ids))
        rows = (await session.scalars(stmt.order_by(JDRaw.id.asc()).limit(limit))).all()

        # 已抽取记录视图：(jd_id, position, publish_date, skills)
        views = []
        for row in rows:
            ext = _extraction_of(row)
            if not ext:
                continue
            publish = _publish_date(row.snapshot or {}, row.crawled_at or "")
            skills = _skills_of(ext)
            if not skills or publish is None:
                continue
            views.append((row, (row.id, ext.get("position_name") or "", publish, skills)))

        results: dict = {"checked": 0, "skipped": len(rows) - len(views), "flagged": []}
        for row, (jd_id, position, publish, skills) in views:
            # views 元素为 (row, (jd_id, position, publish, skills))，需按第二层解包
            group = [
                (r_id, r_publish, r_skills)
                for _, (r_id, r_position, r_publish, r_skills) in views
                if r_position == position
            ]
            skill_ages = _skill_first_seen_days(group, skills, today)
            if not skill_ages:
                results["skipped"] += 1
                continue

            # 同岗位近 90 天窗口的技能首见时长聚合，作为 SAI 基线
            recent_ages = [
                age
                for _, pdate, gs in group
                if (today - pdate).days <= RECENT_WINDOW_DAYS
                for age in _skill_first_seen_days(group, gs, today)
            ]
            sai = classify_sai(compute_sai(skill_ages, recent_ages))

            history_skills = [
                set(gs)
                for _, pdate, gs in sorted(group, key=lambda g: g[1])
                if gs != skills
            ]
            zombie = detect_zombie_jd(history_skills, set(skills), sai.sai)

            oldest = min(group, key=lambda g: g[1])
            plagiarism = None
            if oldest[0] != jd_id:
                plagiarism = detect_plagiarism(
                    JDSkillSet(jd_id=str(jd_id), position_name=position, publish_date=publish, skills=skills),
                    JDSkillSet(jd_id=str(oldest[0]), position_name=position, publish_date=oldest[1], skills=oldest[2]),
                )

            decay = apply_temporal_decay(1.0, sai, zombie, plagiarism)
            snap = dict(row.snapshot or {})
            snap["validation"] = {
                "sai": sai.model_dump(),
                "zombie": zombie.model_dump(),
                "plagiarism": plagiarism.model_dump() if plagiarism else None,
                "decay_weight": decay,
            }
            row.snapshot = snap
            results["checked"] += 1
            flagged = sai.label != "fresh" or zombie.is_zombie or (plagiarism is not None and plagiarism.is_plagiarism)
            if flagged:
                results["flagged"].append({
                    "jd_id": jd_id,
                    "position": position,
                    "sai": sai.label,
                    "zombie": zombie.is_zombie,
                    "plagiarism": plagiarism.is_plagiarism if plagiarism else False,
                    "decay_weight": decay,
                })
        await session.commit()

    return results


async def detect_inflation(
    ctx: dict,
    jd_ids: list[int] | None = None,
    limit: int = 200,
) -> dict:
    """通胀检测（设计文档 §4.8）：从 jd_raw + LLM 抽取结果接入四维通胀评分。

    输入：extraction.level（岗位级别）/ education / requirements（数量 + 专家级数量）
         + snapshot.experience（最小年限，如 "3-5年" → 3）。
    结果写回 `snapshot["inflation"]`（含四维分 / inflation_score / label / decay_weight）。
    缺岗位级别或经验解析失败的 JD 跳过，不做武断判定。
    """
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.raw import JDRaw
    from app.services.data_quality.inflation_detector import compute_inflation_score

    async with async_session_factory() as session:
        stmt = select(JDRaw).where(JDRaw.snapshot["extraction"].astext.isnot(None))
        if jd_ids:
            stmt = stmt.where(JDRaw.id.in_(jd_ids))
        rows = (await session.scalars(stmt.order_by(JDRaw.id.asc()).limit(limit))).all()

        results: dict = {"checked": 0, "skipped": 0, "flagged": []}
        for row in rows:
            ext = _extraction_of(row)
            if not ext:
                results["skipped"] += 1
                continue
            level = ext.get("level") or ""
            if level not in _QUALITY_LEVELS:
                results["skipped"] += 1
                continue
            min_years = _experience_years(row.snapshot or {})
            if min_years is None:
                results["skipped"] += 1
                continue

            reqs = ext.get("requirements") or []
            skill_count = len(reqs) if reqs else len(ext.get("skills") or [])
            expert_count = sum(1 for r in reqs if r.get("level") == "专家")
            edu = (ext.get("education") or {}).get("level") or "不限"
            inflation = compute_inflation_score(level, min_years, skill_count, expert_count, edu)

            snap = dict(row.snapshot or {})
            snap["inflation"] = inflation.model_dump()
            row.snapshot = snap
            results["checked"] += 1
            if inflation.label != "normal":
                results["flagged"].append({
                    "jd_id": row.id,
                    "label": inflation.label,
                    "inflation_score": inflation.inflation_score,
                })
        await session.commit()

    return results


async def load_courses(ctx: dict) -> dict:
    """课程数据入图（course_raw → Course/Skill 节点 + LEARNABLE_VIA 关系）。

    遍历 course_raw.snapshot 调 import_course（Neo4j MERGE 幂等，重复执行
    不产生重复节点）。单条失败不阻塞整体（批量语义）。
    """
    from sqlalchemy import select

    from app.core.database import async_session_factory, neo4j_driver
    from app.models.raw import CourseRaw
    from app.services.kg.kg_service import import_course

    async with async_session_factory() as session:
        rows = (await session.scalars(select(CourseRaw))).all()
    data = [dict(r.snapshot or {}) for r in rows]

    imported = 0
    failed = 0
    with neo4j_driver.session() as neo4j_session:
        for course_data in data:
            try:
                import_course(neo4j_session, course_data)
                imported += 1
            except Exception:
                failed += 1
    return {"total": len(data), "imported": imported, "failed": failed}


async def evaluate_courses(ctx: dict) -> dict:
    """课程质量评估（DA-M4-01，设计文档 §4.6）。

    遍历 course_raw 全量课程 → 六维加权质量评分 → 幂等写回
    `snapshot["quality"]`（覆盖更新）。返回推荐池统计，供学习路径取 Top-3。
    """
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.raw import CourseRaw
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
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.raw import CommunityRaw, CourseRaw, JDRaw, PaperRaw
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
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.raw import CommunityRaw, CourseRaw, JDRaw, PaperRaw
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
    print(f"[check_data_freshness] 报告已写入: {report_path} 过期来源: {stale}", flush=True)
    return {"report_path": str(report_path), "stale_sources": stale}


async def aggregate_positions(ctx: dict) -> dict:
    """岗位聚合（设计文档 §5.5）：jd_raw 抽取结果 → Position 热度 + REQUIRES 边权重。

    全量重算，幂等（覆盖写回 Neo4j）：
    - Position.freq / required_years / last_updated
    - REQUIRES.weight / necessity / source_count
    """
    from sqlalchemy import select

    from app.core.database import async_session_factory, neo4j_driver
    from app.models.raw import JDRaw
    from app.services.kg.aggregation import build_aggregates, write_aggregates

    async with async_session_factory() as session:
        rows = (await session.scalars(
            select(JDRaw).where(JDRaw.snapshot["extraction"].astext.isnot(None))
        )).all()

    now = datetime.now(timezone(timedelta(hours=8))).isoformat()
    agg = build_aggregates(rows)
    return write_aggregates(neo4j_driver.session(), agg, now)


async def cross_validate_jds(ctx: dict, limit: int | None = None) -> dict:
    """多平台交叉验证（DA-M3-03，设计文档 §4.5）。

    聚合 jd_raw 已抽取记录按归一化岗位名分组，校验技能一致性（≥2 源印证）、
    薪资异常、经验分歧、跨源置信度，结果写回 `snapshot["cross_validation"]`
    （幂等覆盖）。返回组级统计供管线审计。
    """
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.raw import JDRaw
    from app.services.data_quality.cross_validate import (
        build_position_groups,
        validate_group,
    )
    from app.services.extraction.dictionary import normalize_position_name

    async with async_session_factory() as session:
        stmt = select(JDRaw).where(JDRaw.snapshot["extraction"].astext.isnot(None))
        rows = (await session.scalars(stmt.order_by(JDRaw.id.asc()))).all()
        if limit:
            rows = rows[:limit]

        records = [
            {"snapshot": r.snapshot or {}, "source": r.source, "crawled_at": r.crawled_at}
            for r in rows
        ]
        results = [
            validate_group(pos, group)
            for pos, group in build_position_groups(records).items()
        ]

        group_map = {r.position_name: r for r in results}
        written = 0
        for row in rows:
            ext = (row.snapshot or {}).get("extraction") or {}
            result = group_map.get(normalize_position_name(ext.get("position_name") or ""))
            if result is None:
                continue
            snap = dict(row.snapshot or {})
            snap["cross_validation"] = result.model_dump()
            row.snapshot = snap
            written += 1
        await session.commit()

    return {
        "groups": len(results),
        "multi_source": sum(1 for r in results if r.source_count >= 2),
        "verified": sum(1 for r in results if r.verified),
        "below_confidence": sum(1 for r in results if r.confidence < 0.6),
        "written": written,
    }


async def run_etl_pipeline(ctx: dict, run_date: str | None = None) -> dict:
    """编排完整 ETL 管线（设计文档 §4.4）。

    管线顺序：
        crawl_jds → clean_jds(已在 Scrapy Pipeline 内嵌) → dedup
        → validate_temporal → detect_inflation → structure → load_to_db
        → load_to_neo4j（含课程入图 + 岗位聚合）

    M2 阶段：仅执行 crawl_jds + 框架占位任务（structure/load 依赖 M3 LLM 抽取）
    M3 阶段：完整管线启用

    Args:
        run_date: 调度日期 YYYY-MM-DD，None 时取 UTC+8 当日
    """
    if run_date is None:
        run_date = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

    # 按设计文档 §4.4 数据更新频率分组
    # 国内 A 级 + B 级（02:00 / 04:00）
    domestic_platforms = ["boss", "zhilian"]
    # 国际 A/B 级（错峰）
    international_platforms = ["monster", "indeed", "glassdoor"]
    # 非招聘数据源（论文/社区/课程）
    trend_platforms = ["arxiv", "github", "stackoverflow"]

    results: dict = {
        "run_date": run_date,
        "stages": {},
    }

    # ── 阶段 1：爬虫（A 级国内主源）──
    crawl_results = []
    for spider in domestic_platforms + international_platforms + trend_platforms:
        try:
            r = await crawl_platform(ctx, spider)
            crawl_results.append(r)
        except Exception as e:
            # 单源失败不阻塞其他源（设计文档「单源失效不影响整体」）
            crawl_results.append({"spider": spider, "error": str(e)})
    results["stages"]["crawl"] = crawl_results

    # ── 阶段 2：清洗 + 去重 ──
    # 已嵌入 Scrapy CleaningPipeline（SHA256 指纹 upsert 即去重）
    # SimHash 去重为 DA-M3-08 遗留项
    results["stages"]["clean_dedup"] = {
        "status": "embedded_in_scrapy_pipeline",
        "simhash_pending": "DA-M3-08",
    }

    # ── 阶段 3：时滞检测（M3 启用）──
    results["stages"]["validate_temporal"] = await validate_temporal(ctx, jd_ids=[])

    # ── 阶段 4：通胀检测（M3 启用）──
    results["stages"]["detect_inflation"] = await detect_inflation(ctx, jd_ids=[])

    # ── 阶段 5：结构化 + 入库（M3 启用：LLM 抽取 → snapshot 写回 → Neo4j 入图）──
    results["stages"]["structure_load"] = await batch_extract(ctx, limit=500)

    # ── 阶段 6：课程入图（course_raw → Course + LEARNABLE_VIA）──
    results["stages"]["load_courses"] = await load_courses(ctx)

    # ── 阶段 7：课程质量评估（DA-M4-01，六维加权 → 推荐池写回 snapshot["quality"]）──
    results["stages"]["evaluate_courses"] = await evaluate_courses(ctx)

    # ── 阶段 8：岗位聚合（Position.freq + REQUIRES weight/source_count）──
    results["stages"]["aggregate_positions"] = await aggregate_positions(ctx)

    # ── 阶段 9：多平台交叉验证（DA-M3-03，技能跨源印证/薪资异常/置信度）──
    results["stages"]["cross_validate"] = await cross_validate_jds(ctx)

    # ── 阶段 10：数据多样性报告（DA-M4-02，reports/diversity_{date}.json）──
    results["stages"]["diversity_report"] = await diversity_report(ctx)

    # ── 阶段 11：数据更新新鲜度检查（DA-M4-03，T+1 承诺审计）──
    results["stages"]["check_data_freshness"] = await check_data_freshness(ctx)

    # ── 阶段 12：发布图谱版本快照（§7.1 T+1 版本管理，05:00 前自动发布）──
    results["stages"]["snapshot_graph"] = await snapshot_graph(ctx, triggered_by="scheduled")

    return results


# ============================================================
# 业务异步任务（M3/M4 实现）
# ============================================================

async def resume_parse(ctx: dict, file_path: str, task_id: str | None = None) -> dict:
    """简历解析异步任务（M4 实现）。

    流程：文件文本抽取 → PII 脱敏 → LLM 抽取（规则兜底）→ 画像落库 resume_cache。
    - 结果按 file_hash upsert 到 resume_cache（幂等，重复执行覆盖更新）
    - 任务状态经 TaskStatus 追踪（parse_resume 路由入队时携带 task_id）
    - 任一环节失败标记 task failed 并记录错误，不做假成功返回
    """
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.business import ResumeCache, TaskStatus
    from app.services.resume.extractor import ResumeExtractor
    from app.services.resume.file_parser import extract_text
    from app.services.resume.pii_mask import mask_pii

    async with async_session_factory() as session:
        task = await session.get(TaskStatus, task_id) if task_id else None
        if task is None:
            # 兼容未携带 task_id 的旧入队：按 result.file_path 定位
            task = await session.scalar(
                select(TaskStatus).where(
                    TaskStatus.result["file_path"].astext == str(file_path)
                )
            )
        if task is None:
            return {"status": "failed", "error": "TaskStatus 不存在"}

        result_info = task.result or {}
        task.status = "running"
        task.progress = 0.2
        await session.commit()

        try:
            # 1. 文件文本抽取（pdf/docx/txt；扫描件抛 ResumeParseError）
            text = extract_text(file_path)

            # 2. PII 脱敏（送入 LLM 前必须先脱敏，设计文档 §8.2）
            masked, _mapping = mask_pii(text)

            # 3. LLM 结构化抽取（无 api_key / 全 provider 失败降级规则抽取）
            task.progress = 0.6
            await session.commit()
            result = ResumeExtractor().extract(masked)

            # 4. 画像落库（按 file_hash upsert，幂等可重跑）
            parsed = result.model_dump()
            cache = await session.scalar(
                select(ResumeCache).where(ResumeCache.file_hash == result_info["file_hash"])
            )
            if cache is None:
                cache = ResumeCache(
                    file_hash=result_info["file_hash"],
                    file_name=result_info.get("file_name") or Path(file_path).name,
                    parsed_data=parsed,
                )
                session.add(cache)
            else:
                cache.parsed_data = parsed
                cache.version += 1
            await session.flush()

            task.status = "success"
            task.progress = 1.0
            task.result = {
                "resume_id": str(cache.id),
                "skills": [s.get("name") for s in parsed.get("skills", []) if s.get("name")],
            }
        except Exception as e:
            task.status = "failed"
            task.error = str(e)[:500]
        await session.commit()

        if task.status == "success":
            return {"status": "success", "resume_id": task.result["resume_id"]}
        return {"status": "failed", "error": task.error}


# 参与 JD 正文拼接的 snapshot 字段（按此顺序，跳过来源无关的元数据）
_JD_TEXT_FIELDS = (
    "title", "company", "location", "salary", "experience",
    "education", "description", "requirements",
)

# 批量连续调用 LLM 易触发 provider 限流（429，实测 deepseek 批量 100 条大量失败、
# 单条重跑全成功），每条请求间隔平滑突发；批量任务允许的额外耗时（100 条 ≈ 30s）
_BATCH_REQUEST_INTERVAL = 0.3


def _build_jd_text(snapshot: dict, raw_text: str) -> str:
    """拼装 JD 抽取正文。

    优先 snapshot 的干净文本字段（raw_text 为原始 HTML/JSON 备份，不适合直接喂 LLM）；
    但正文字段（description/requirements）缺失时拼接结果过短无法抽取，
    此时回退 raw_text（黄金集等数据正文可能只存在 raw_text 中）。
    """
    body_fields = (snapshot.get("description"), snapshot.get("requirements"))
    if not any(str(f or "").strip() for f in body_fields):
        return raw_text
    parts = [str(snapshot.get(f, "")).strip() for f in _JD_TEXT_FIELDS]
    return "\n".join(p for p in parts if p)


async def batch_extract(
    ctx: dict,
    jd_ids: list[int] | None = None,
    limit: int = 100,
) -> dict:
    """LLM 批量实体抽取 + JD 入图（M3 实现，依赖 AL-M3-01）。

    选取 jd_raw 中尚未抽取（snapshot 无 extraction 标记）的记录：
    - 拼装 JD 正文 → JDExtractor.extract（instructor 强校验，失败单条降级规则抽取）
    - 抽取结果写回 jd_raw.snapshot["extraction"]（可审计、可重跑）
    - kg_service.import_jd 入图（Neo4j MERGE 幂等，重复执行不产生重复节点）

    单条失败不阻塞整体（批量语义）；全部失败时抛出，由 ARQ 重试机制兜底。
    """
    from sqlalchemy import select

    from app.core.database import async_session_factory, neo4j_driver
    from app.models.raw import JDRaw
    from app.services.extraction.jd_extractor import JDExtractor
    from app.services.kg.kg_service import import_jd

    extractor = JDExtractor()

    async with async_session_factory() as session:
        if jd_ids:
            rows = (await session.scalars(
                select(JDRaw).where(JDRaw.id.in_(jd_ids))
            )).all()
        else:
            # 未抽取 = snapshot 无 extraction 键（JSONB 键缺失时为 SQL NULL）
            rows = (await session.scalars(
                select(JDRaw)
                .where(JDRaw.snapshot["extraction"].astext.is_(None))
                .order_by(JDRaw.id.asc())
                .limit(limit)
            )).all()

        results: dict = {"processed": 0, "succeeded": 0, "failed": [], "positions": []}
        for row in rows:
            await asyncio.sleep(_BATCH_REQUEST_INTERVAL)
            text = _build_jd_text(row.snapshot or {}, row.raw_text or "")
            if len(text.strip()) < 10:
                results["failed"].append({"jd_id": row.id, "error": "JD 正文过短（<10 字符），跳过"})
                continue
            results["processed"] += 1
            try:
                extraction = extractor.extract(text)
                snap = dict(row.snapshot or {})
                snap["extraction"] = extraction.model_dump()
                row.snapshot = snap
                evidence = {
                    "source": row.source,
                    "source_url": row.source_url,
                    "crawled_at": row.crawled_at,
                    "raw_text": text,
                }
                with neo4j_driver.session() as neo4j_session:
                    position_id = import_jd(neo4j_session, extraction, evidence)
                results["succeeded"] += 1
                results["positions"].append({"jd_id": row.id, "position_id": position_id})
            except Exception as e:
                results["failed"].append({"jd_id": row.id, "error": str(e)[:500]})
        await session.commit()

    if results["processed"] > 0 and results["succeeded"] == 0:
        raise RuntimeError(f"批量抽取全部失败: {results['failed'][:5]}")
    return results


async def evolution_compute(ctx: dict, version: str) -> dict:
    """每日演化计算异步任务（M3 实现，当前未交付）。"""
    raise NotImplementedError("evolution_compute 待 AL-M3 演化管线接入后实现")


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


async def discovery_daily(ctx: dict) -> dict:
    """每日新岗位发现（AL-M4-01，设计文档 7.2.3 节）。

    流程：聚合 jd_raw 已抽取记录 → 计算候选特征（freq/源多样性/Z-score）
    → 阶段一门控（detect_candidates）→ 阶段二 RAG 接地（权威库 + 种子）
    → 幂等 upsert discovery_candidates 候选池 → 自动状态流转持久化。

    幂等设计：按 position_name upsert，重复执行覆盖更新（同岗位不重复入池）。
    """
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.raw import JDRaw
    from app.services.discovery.detector import DiscoveryDetector, DiscoveryInput
    from app.services.discovery.confidence import compute_confidence
    from app.services.extraction.dictionary import normalize_position_name
    from app.services.discovery.schemas import DiscoveryFeatures

    # ── 1. 聚合 jd_raw 已抽取记录 → 岗位频次/源多样性 ──
    async with async_session_factory() as session:
        rows = (await session.scalars(
            select(JDRaw).where(JDRaw.snapshot["extraction"].astext.isnot(None))
        )).all()

    position_stats: dict[str, dict] = {}
    for row in rows:
        ext = (row.snapshot or {}).get("extraction") or {}
        name = normalize_position_name(ext.get("position_name") or "")
        if not name:
            continue
        stat = position_stats.setdefault(name, {"count": 0, "sources": set()})
        stat["count"] += 1
        stat["sources"].add(row.source)

    if not position_stats:
        return {"candidates": 0, "detail": "无已抽取岗位记录"}

    # ── 2. 组装 DiscoveryInput（Z-score 数据不足 90 天 → 冷启动 Wilson 兜底）──
    jd_total = sum(s["count"] for s in position_stats.values())
    inputs = []
    for name, stat in position_stats.items():
        freq = float(stat["count"])
        # 单日窗口无法计算 Z-score，走冷启动（source_diversity ≥ 3 触发）
        inputs.append(
            DiscoveryInput(
                position_name=name,
                features=DiscoveryFeatures(
                    jd_freq_ma3=freq,
                    z_score=None,
                    source_diversity=len(stat["sources"]),
                ),
                history_days=1,
                cold_successes=stat["count"],
                cold_total=jd_total,
            )
        )

    # ── 3. 阶段一门控 + 阶段二 RAG 接地 ──
    detector = DiscoveryDetector()
    candidates = detector.detect_candidates(_Provider(inputs))
    grounded = []
    async with async_session_factory() as session:
        for cand in candidates:
            c = await detector.ground_with_rag(cand, session)
            # 置信度：冷启动样本下用来源多样性近似（无历史 Z-score 时 base 恒 0）
            conf = compute_confidence(
                jd_count=int(cand.features.jd_freq_ma3),
                source_count=cand.features.source_diversity,
                growth_rate=0.0,
            )
            c = c.model_copy(update={"confidence": conf})
            grounded.append(c)
            await _upsert_candidate(session, c)
        await session.commit()

    # 注：自动态迁移（emerging→stable / declining 等）依赖历史窗口 freq 序列，
    # 当前单日快照不提供窗口表；candidate→emerging/rejected 由 admin 审核端点
    # 调用状态机评估，每日任务只负责入池与 RAG 接地。

    return {
        "candidates": len(grounded),
        "seed_matched": sum(1 for c in grounded if c.seed_matched),
        "rag_matched": sum(1 for c in grounded if c.rag_matched),
    }


class _Provider:
    """适配 CandidateProvider Protocol 的内存数据源。"""

    def __init__(self, inputs):
        self._inputs = inputs

    def iter_inputs(self):
        return iter(self._inputs)


async def _upsert_candidate(session, cand) -> None:
    """按 position_name upsert 候选池（幂等：同岗位覆盖更新特征/状态）。"""
    from app.models.business import DiscoveryCandidate
    from sqlalchemy import select

    row = await session.scalar(
        select(DiscoveryCandidate).where(DiscoveryCandidate.position_name == cand.position_name)
    )
    payload = {
        "state": cand.state.value,
        "features": cand.features.model_dump() if hasattr(cand.features, "model_dump") else cand.features,
        "confidence": cand.confidence.model_dump() if cand.confidence else {},
        "evidence_refs": cand.evidence_refs,
        "seed_matched": cand.seed_matched,
        "rag_matched": cand.rag_matched,
        "definition_draft": cand.definition_draft,
        "detected_at": cand.detected_at,
    }
    if row is None:
        session.add(DiscoveryCandidate(id=cand.candidate_id, position_name=cand.position_name, **payload))
    else:
        for k, v in payload.items():
            setattr(row, k, v)


# ============================================================
# ARQ Worker 注册
# ============================================================

async def on_startup(ctx: dict) -> None:
    """Worker 启动钩子。"""
    print(f"[ARQ Worker] 启动，PID={ctx.get('worker_pid')}")


async def on_shutdown(ctx: dict) -> None:
    """Worker 关闭钩子。"""
    print("[ARQ Worker] 关闭")


class WorkerSettings:
    """ARQ Worker 配置。

    启动命令：arq app.workers.tasks.WorkerSettings
    """
    functions = [
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
        snapshot_graph,
        evolution_compute,
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(settings.arq_redis_url)
    concurrency = settings.arq_concurrency
    task_timeout = settings.arq_task_timeout
    max_retries = 2
    retry_delay = 10
