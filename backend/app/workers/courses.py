"""Course enrichment/import/quality ARQ tasks.

Ownership: course-related ARQ tasks (enrich_course_skills / load_courses /
evaluate_courses) and their retry constants. All names are re-exported from
``app.workers.tasks`` (facade) so WorkerSettings registration and existing
``from app.workers.tasks import ...`` imports keep working — function names
(``__qualname__``) must stay identical for ARQ job matching.
"""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import or_, select

from app.models.raw import CourseRaw


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
