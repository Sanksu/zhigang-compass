"""Course enrichment/import/quality ARQ tasks.

Ownership: course-related ARQ tasks (enrich_course_skills / load_courses /
evaluate_courses) and their retry constants. All names are re-exported from
``app.workers.tasks`` (facade) so WorkerSettings registration and existing
``from app.workers.tasks import ...`` imports keep working — function names
(``__qualname__``) must stay identical for ARQ job matching.
"""

import asyncio
import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import or_, select

from app.models.raw import CourseRaw

# 入图增量指纹（08-23 闭环收敛 P1-1）：import_course 消费的快照字段——
# 这些字段不变时 MERGE 结果不变，跳过重复导入；quality/skills_enriched
# 等阶段自写字段刻意排除（否则评估回写会触发每日全量重导）。
_IMPORT_FIELDS = (
    "source", "source_id", "title", "institution", "platform",
    "category", "description", "rating", "enrollment",
    "duration", "source_url", "skills",
)

# 质量评估输入字段（evaluate_course 六维评分的实际输入 + title 标签）
_EVAL_FIELDS = (
    "title", "platform", "rating", "enrollment",
    "start_date", "skills", "description",
)


def _fingerprint(snap: dict, fields: tuple[str, ...]) -> str:
    payload = {k: snap.get(k) for k in fields}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


# ============================================================
# ETL 阶段任务
# ============================================================


# 课程技能抽取（enrich_course_skills）失败重试配置（08-16 用户要求）：
# 单课程 LLM 抽取失败后延迟 _ENRICH_RETRY_DELAY_SECONDS 秒再次进入队列
# （下次 ETL 阶段 5.5 到期才重试，避免瞬时故障风暴下每次 ETL 全量重试）；
# 累计失败达 _ENRICH_MAX_FAILS 次后放弃（写 skills_enriched，防无限重试）。
_ENRICH_RETRY_DELAY_SECONDS = 3600   # 失败后延迟 1 小时重试
_ENRICH_MAX_FAILS = 3                # 累计失败 3 次放弃

# 滞后重抽（08-31）：LLM 正常判定"无技能"（宁少勿滥空数组）时不立即永久标记，
# 而是进入冷却后重抽池（区别于 LLM 异常失败路径的 1h/3 次）。延迟 24h 后再次
# 尝试，累计 _EMPTY_MAX_RETRIES 次仍为空才永久放弃（防无限重抽）。空判定与异常
# 失败各自独立计数（skills_enrich_empty_fails / skills_enrich_fails，08-31 修复：
# 共用 skills_enrich_fails 会让历史 error 抬高空路径放弃计数——1 error + 首个空即
# 误判永久放弃，违背"共 3 次机会"语义），放弃阈值与冷却时长独立配置。
_EMPTY_RETRY_DELAY_SECONDS = 86400   # 判定为空后延迟 24 小时重抽
_EMPTY_MAX_RETRIES = 2               # 判定为空累计 2 次后放弃（共 3 次机会）


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
            # 滞后重抽成功后清除旧的冷却时间戳（计数保留供排查）
            snap.pop("skills_retry_at", None)
        elif llm is None:
            # LLM 不可用（skipped_no_llm）：不写标记——配置恢复后自动重试，
            # 避免"LLM 缺失期间误标已处理"导致课程永久无标签
            continue
        elif llm_errored:
            # 异常失败（未达放弃上限）：不写标记——retry_at 到期后重入队
            pass
        else:
            # LLM 正常判定无技能（宁少勿滥空数组）：不立即永久标记，进入滞后
            # 重抽池——写计数 + 24h 冷却时间戳，原点 skills_enriched IS NULL
            # 保持入选资格，待冷却到期重抽；达上限才永久放弃（防无限重抽）。
            # 空判定独立计数（与技能失败 skills_enrich_fails 分开）：历史 error
            # 不抬高空阈值，首个空从 0 起算，达 _EMPTY_MAX_RETRIES 才永久放弃
            empty_fails = int(snap.get("skills_enrich_empty_fails") or 0) + 1
            snap["skills_enrich_empty_fails"] = empty_fails
            if empty_fails >= _EMPTY_MAX_RETRIES:
                snap["skills_enriched"] = True
            else:
                snap["skills_retry_at"] = (
                    datetime.now(timezone(timedelta(hours=8)))
                    + timedelta(seconds=_EMPTY_RETRY_DELAY_SECONDS)
                ).isoformat()
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

    增量导入（08-23 闭环收敛 P1-1）：入图相关字段的指纹存 Course.import_hash，
    与库中一致即跳过（MERGE 幂等前提下免每日全量重导）；缺 source/source_id
    的行无法定位指纹键，保守走全量导入。单条失败不阻塞整体（批量语义）。
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
        skipped = 0
        with neo4j_driver.session() as neo4j_session:
            existing: dict[tuple[str, str], str] = {}
            for rec in neo4j_session.run(
                "MATCH (c:Course) RETURN c.source AS s, c.source_id AS sid, c.import_hash AS h"
            ).data():
                if rec.get("h"):
                    existing[(rec.get("s") or "", rec.get("sid") or "")] = rec["h"]
            for course_data in data:
                source = course_data.get("source") or ""
                source_id = course_data.get("source_id") or ""
                fp = _fingerprint(course_data, _IMPORT_FIELDS) if source and source_id else None
                if fp and existing.get((source, source_id)) == fp:
                    skipped += 1
                    continue
                try:
                    import_course(neo4j_session, course_data)
                    if fp:
                        neo4j_session.run(
                            "MATCH (c:Course {source: $s, source_id: $sid}) "
                            "SET c.import_hash = $fp",
                            s=source, sid=source_id, fp=fp,
                        )
                        existing[(source, source_id)] = fp
                    imported += 1
                except Exception:
                    failed += 1
        return imported, failed, skipped

    imported, failed, skipped = await asyncio.to_thread(_import_all)
    return {"total": len(data), "imported": imported, "failed": failed, "skipped": skipped}


async def evaluate_courses(ctx: dict) -> dict:
    """课程质量评估（DA-M4-01，设计文档 §4.6）。

    增量评估（08-23 闭环收敛 P1-1）：六维输入字段指纹存
    snapshot.quality.input_hash，与上次一致即复用已存评分（不重算不重写，
    消除每日全量 PG 写放大）；输入变化或历史记录无指纹时重评一次。
    """

    from app.core.database import async_session_factory
    from app.services.data_quality.course_quality import (
        RECOMMEND_MIN_SCORE,
        CourseQualityResult,
        evaluate_course,
    )

    async with async_session_factory() as session:
        rows = (await session.scalars(select(CourseRaw).order_by(CourseRaw.id.asc()))).all()
        results: list[CourseQualityResult] = []
        for row in rows:
            snap = dict(row.snapshot or {})
            fp = _fingerprint(snap, _EVAL_FIELDS)
            prev = snap.get("quality")
            if isinstance(prev, dict) and prev.get("input_hash") == fp:
                # 输入未变：复用已存评分（不回写 snapshot，零写放大）
                try:
                    results.append(CourseQualityResult.model_validate(prev))
                    continue
                except Exception:
                    pass  # 历史结构异常 → 落到重评
            result = evaluate_course(snap)
            snap["quality"] = {**result.model_dump(), "input_hash": fp}
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
