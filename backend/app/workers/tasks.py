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
    _QUALITY_LEVELS as _QUALITY_LEVELS,
    _experience_years as _experience_years,
    _extraction_of as _extraction_of,
    _graph_skill_first_seen as _graph_skill_first_seen,
    _history_skill_sets as _history_skill_sets,
    _publish_date as _publish_date,
    _skill_first_seen_days as _skill_first_seen_days,
    _skills_of as _skills_of,
    _snapshot_with_skip as _snapshot_with_skip,
    detect_inflation as detect_inflation,
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


def _purge_dup_import_residue(urls: list[str]) -> dict:
    """清除已入图 SimHash 重复记录的图谱残留（08-15 核查后新增）。

    重复记录在 canonical 名下入图即可，其独立入图残留 = 岗位节点 + 空权
    REQUIRES 边（import_jd 写 necessity/level，聚合跳过重复记录 → 永不获
    weight/source_count）。规则：
    1. 删记录 Evidence 的 HAS_EVIDENCE（岗位）边；Evidence 被技能
       EVIDENCED_BY 引用时保留节点（证据追溯链完整），否则连带删除；
    2. 受影响岗位删除后无任何证据且 REQUIRES 均无 source_count → 纯重复
       残留，DETACH DELETE（空权边一并清除）。

    Returns:
        {"has_edges_removed", "evidence_removed", "positions_removed"}
    """
    if not urls:
        return {"has_edges_removed": 0, "evidence_removed": 0, "positions_removed": 0}
    from app.core.database import neo4j_driver

    with neo4j_driver.session() as session:
        # 先收集受影响岗位（须在删证据边之前，删后无法回溯归属）
        affected = session.run(
            """
            MATCH (p:Position)-[:HAS_EVIDENCE]->(e:Evidence)
            WHERE e.source_url IN $urls
            RETURN collect(DISTINCT p.name) AS names
            """,
            urls=urls,
        ).single()["names"]
        if not affected:
            return {"has_edges_removed": 0, "evidence_removed": 0, "positions_removed": 0}

        has_edges_removed = session.run(
            """
            MATCH (:Position)-[h:HAS_EVIDENCE]->(e:Evidence)
            WHERE e.source_url IN $urls
            RETURN count(h) AS n
            """,
            urls=urls,
        ).single()["n"]
        session.run(
            """
            MATCH (:Position)-[h:HAS_EVIDENCE]->(e:Evidence)
            WHERE e.source_url IN $urls
            DELETE h
            """,
            urls=urls,
        )
        evidence_removed = session.run(
            """
            MATCH (e:Evidence) WHERE e.source_url IN $urls
            WITH e
            OPTIONAL MATCH (sk:Skill)-[eb:EVIDENCED_BY]->(e)
            WITH e, count(eb) AS refs
            WHERE refs = 0
            DETACH DELETE e
            RETURN count(e) AS n
            """,
            urls=urls,
        ).single()["n"]
        positions_removed = session.run(
            """
            UNWIND $names AS name
            MATCH (p:Position {name: name})
            WHERE NOT EXISTS { MATCH (p)-[:HAS_EVIDENCE]->(:Evidence) }
              AND NOT EXISTS {
                  MATCH (p)-[r:REQUIRES]->(:Skill) WHERE r.source_count IS NOT NULL
              }
            DETACH DELETE p
            RETURN count(p) AS n
            """,
            names=affected,
        ).single()["n"]
    return {
        "has_edges_removed": has_edges_removed,
        "evidence_removed": evidence_removed,
        "positions_removed": positions_removed,
    }


async def dedup_simhash(ctx: dict, limit: int | None = None) -> dict:
    """SimHash 跨平台近似去重（设计文档 §4.2 消费方）。

    扫描 jd_raw 已入库记录的 snapshot->_simhash（CleaningPipeline 采集时写入，
    基于脱敏后文本），批量 find_similar_pairs（汉明距 ≤ 3）找出近似重复 JD。
    jd_embeddings 语义辅助（§11.4.3）：两记录的向量余弦 < 0.9 视为语义不相似，
    不标记重复（降低 SimHash 误判）；向量缺失时保留 SimHash 判定。
    将后入库记录标记 `snapshot["_duplicate_of"]` = 先入库记录 id。
    聚合层（aggregation.build_aggregates）跳过被标记记录，避免重复 JD 虚高频次。
    """

    from app.core.database import async_session_factory
    from app.services.data_quality.simhash import find_similar_pairs
    from app.services.embeddings.vector_store import load_jd_vectors_by_ids
    from app.services.matching.semantic import cosine_similarity

    # JD 语义去重辅助阈值（§11.4.3 jd_embeddings Cosine）：低于该值不标记
    _EMBED_DEDUP_THRESHOLD = 0.9

    async with async_session_factory() as session:
        # 只加载带 _simhash 的记录，避免全表拉取（审查 major：dedup_simhash 全表加载）
        stmt = select(JDRaw).where(
            JDRaw.snapshot["_simhash"].astext.isnot(None),
        )
        if limit:
            stmt = stmt.limit(limit)
        stmt = stmt.order_by(JDRaw.id.asc())
        rows = (await session.scalars(stmt)).all()

        records: list[tuple[str, int]] = []
        for r in rows:
            sh = (r.snapshot or {}).get("_simhash")
            if isinstance(sh, int) and sh:
                records.append((str(r.id), sh))

        pairs = find_similar_pairs(records)

        # 语义辅助：仅加载 pairs 涉及 jd 的向量（08-14 审查：此前全量加载
        # jd_embeddings 入内存；pairs 通常远少于全量记录数）
        pair_ids = sorted({i for p in pairs for i in p})
        emb_map = await load_jd_vectors_by_ids(session, pair_ids)
        verified_pairs: list[tuple[str, str]] = []
        skipped_emb = 0
        for id_a, id_b in pairs:
            va, vb = emb_map.get(id_a), emb_map.get(id_b)
            if va is not None and vb is not None:
                if cosine_similarity(va, vb) < _EMBED_DEDUP_THRESHOLD:
                    skipped_emb += 1
                    continue  # 语义不相似，SimHash 误判，不标记重复
            verified_pairs.append((id_a, id_b))

        # pairs 顺序即 records 输入顺序（id 升序），先入库者保留，后入库者标记
        id_map = {str(r.id): r for r in rows}
        marked = 0
        for id_a, id_b in verified_pairs:
            dup = id_map.get(id_b)
            if dup is None:
                continue
            snap = dict(dup.snapshot or {})
            if snap.get("_duplicate_of") != id_a:
                snap["_duplicate_of"] = id_a
                dup.snapshot = snap
                marked += 1
        await session.commit()

        # 入图残留对齐清理（08-15 新增）：去重标记可能晚于抽取入图（重复对
        # 在后续轮次才发现），已入图的重复记录残留岗位节点 + 空权 REQUIRES 边。
        # 与 rebuild_graph/聚合口径一致清除；已抽取记录才可能入过图，未抽取
        # （跳过/失败）记录在图中无残留，无需处理。
        dup_urls = [
            (r.snapshot or {}).get("source_url") or r.source_url
            for id_a, id_b in verified_pairs
            if (r := id_map.get(id_b)) is not None
            and (r.snapshot or {}).get("extraction")
        ]
        purge_stats: dict = {}
        if dup_urls:
            purge_stats = await asyncio.to_thread(_purge_dup_import_residue, dup_urls)

    return {
        "checked": len(records),
        "pairs": len(pairs),
        "skipped_emb": skipped_emb,
        "marked": marked,
        "purged": purge_stats,
    }


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


async def aggregate_positions(ctx: dict) -> dict:
    """岗位聚合（设计文档 §5.5）：jd_raw 抽取结果 → Position 热度 + REQUIRES 边权重。

    全量重算，幂等（覆盖写回 Neo4j）：
    - Position.freq / required_years / last_updated
    - REQUIRES.weight / necessity / source_count
    """

    from app.core.database import async_session_factory, neo4j_driver
    from app.services.kg.aggregation import build_aggregates, write_aggregates

    async with async_session_factory() as session:
        rows = (await session.scalars(
            select(JDRaw).where(JDRaw.snapshot["extraction"].astext.isnot(None))
        )).all()

    now = datetime.now(timezone(timedelta(hours=8))).isoformat()
    # 08-15 审查 M2：build_aggregates 为万级 JD 的同步 CPU 聚合（归一化/权重
    # 计算），原直跑 async 上下文可阻塞 ARQ 事件循环数秒——放线程池
    agg = await asyncio.to_thread(build_aggregates, rows)

    def _write():
        # 同步 Neo4j 写入放线程池，并正确关闭 session（原实现 session 泄漏）
        with neo4j_driver.session() as session:
            return write_aggregates(session, agg, now)

    return await asyncio.to_thread(_write)


async def cross_validate_jds(ctx: dict, limit: int | None = None) -> dict:
    """多平台交叉验证（DA-M3-03，设计文档 §4.5）。

    聚合 jd_raw 已抽取记录按归一化岗位名分组，校验技能一致性（≥2 源印证）、
    薪资异常、经验分歧、跨源置信度，结果写回 `snapshot["cross_validation"]`
    （幂等覆盖）。返回组级统计供管线审计。
    """

    from app.core.database import async_session_factory
    from app.services.data_quality.cross_validate import (
        build_position_groups,
        validate_group,
    )
    from app.services.extraction.position_normalization import normalized_position_from_snapshot

    async with async_session_factory() as session:
        stmt = select(JDRaw).where(JDRaw.snapshot["extraction"].astext.isnot(None))
        rows = (await session.scalars(stmt.order_by(JDRaw.id.asc()))).all()
        if limit:
            rows = rows[:limit]

        records = [
            {"snapshot": r.snapshot or {}, "source": r.source, "crawled_at": r.crawled_at}
            for r in rows
        ]

        def _validate():
            # 纯 CPU 分组校验，放线程池避免阻塞事件循环
            return [
                validate_group(pos, group)
                for pos, group in build_position_groups(records).items()
            ]

        results = await asyncio.to_thread(_validate)

        group_map = {r.position_name: r for r in results}
        written = 0
        for row in rows:
            result = group_map.get(
                normalized_position_from_snapshot(row.snapshot)
            )
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


async def sync_skill_normalization(ctx: dict) -> dict:
    """技能归一化 + SIMILAR_TO 建边（设计文档 §5.3，ETL 阶段 9.5）。

    对图谱全量 Skill 名做 SBERT 层次聚类，回写 `Skill.normalized_name`，
    同簇相似度 ≥ 0.85 自动建 `SIMILAR_TO {similarity}` 关系（幂等 MERGE）。

    模型不可用时归一化退化为词典路径（normalize_skill 在线词典不变），
    不阻塞 ETL 主线。
    """

    def _run():
        # 同步 Neo4j 全量读取 + SBERT 聚类 + 关系回写为 CPU/IO 密集，整体放线程池
        from app.core.database import neo4j_driver
        from app.services.extraction.normalization import (
            SkillNormalizer,
            guard_cluster_distribution,
        )

        with neo4j_driver.session() as session:
            rows = session.run("MATCH (s:Skill) RETURN s.name AS name").data()
            names = [r["name"] for r in rows if r.get("name")]
        if not names:
            return {"skills": 0, "normalized": 0, "similar_pairs": 0, "detail": "图谱无 Skill 节点"}

        normalizer = SkillNormalizer()
        normalized = normalizer.normalize_many(names)
        if not normalized:
            return {"skills": len(names), "normalized": 0, "similar_pairs": 0, "detail": "归一化无输出"}

        # ── 写回前门禁（P0）：簇分布异常拒绝写库，防链式漂移污染图谱 ──
        # 08-13 事故：单链接漂移把 1185 个技能并入"2D可视化"簇后直接入库。
        # 门禁拦截同类异常：巨型簇 / 映射率越界 → 不写库 + 告警 + 返回 blocked
        # （单阶段失败不阻塞 ETL 主线，与 run_etl_pipeline 其余阶段同语义）。
        try:
            guard_cluster_distribution(normalized)  # 门禁校验：异常直接抛 ValueError 拦截
        except ValueError as e:
            msg = f"技能归一化门禁拦截：{e}"
            print(f"[sync_skill_normalization] {msg}", flush=True)
            from app.services.alerting import send_alert

            send_alert("normalization_blocked", msg)
            return {
                "skills": len(names),
                "normalized": 0,
                "similar_pairs": 0,
                "detail": msg,
                "blocked": True,
            }

        changed = sum(1 for n, r in normalized.items() if r.standard != n)
        written = 0
        skipped_standard = 0
        name_set = set(names)  # 图谱现存技能名：过滤 standard 不在图谱的对，避免 MERGE 空匹配丢边
        with neo4j_driver.session() as session:
            # 回写 normalized_name（含自指 SET，幂等）
            for name, res in normalized.items():
                session.run(
                    "MATCH (s:Skill {name: $name}) SET s.normalized_name = $standard",
                    name=name, standard=res.standard,
                )
            # SIMILAR_TO 关系：同标准名组内相似度 ≥ 0.85（§5.3 阈值过滤，非自指）
            for standard, member, sim in normalizer.similar_pairs(normalized):
                if standard not in name_set:
                    skipped_standard += 1
                    continue
                session.run(
                    """
                    MATCH (a:Skill {name: $standard}), (b:Skill {name: $member})
                    MERGE (a)-[r:SIMILAR_TO]->(b)
                    SET r.similarity = $similarity
                    """,
                    standard=standard, member=member, similarity=sim,
                )
                written += 1

        return {
            "skills": len(names),
            "normalized": changed,
            "similar_pairs": written,
            "skipped_standard": skipped_standard,
            "detail": "SIMILAR_TO 已回写（幂等）",
        }

    return await asyncio.to_thread(_run)


async def backfill_embeddings(ctx: dict) -> dict:
    """pgvector 三表向量回填（设计文档 §11.4.3，ETL 阶段 13）。

    从 Neo4j Skill、jd_raw、resume_cache 生成向量写入
    skill_embeddings / jd_embeddings / project_embeddings（幂等）。
    模型不可用时跳过，不阻塞 ETL 主线（语义路降级为关键词/内存相似度）。
    """
    from app.core.database import async_session_factory
    from app.services.embeddings.backfill import run_backfill
    from app.services.matching.semantic import SemanticUnavailableError, SkillEmbedder

    try:
        # 单例首次获取会同步加载 SBERT 模型（可达分钟级），放线程池避免阻塞事件循环
        embedder = await asyncio.to_thread(SkillEmbedder.get)
        async with async_session_factory() as db:
            return await run_backfill(db, embedder)
    except SemanticUnavailableError:
        return {"detail": "语义模型不可用，回填跳过"}


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
# 业务异步任务（M3/M4 实现）
# ============================================================

# 参与 JD 正文拼接的 snapshot 字段（按此顺序，跳过来源无关的元数据）
_JD_TEXT_FIELDS = (
    "title", "company", "location", "salary", "experience",
    "education", "description", "requirements",
)


# LLM 抽取输入上限（08-14：raw_text 去除 65535 入库截断后，超长 JD 需在输入侧
# 裁剪防 context 溢出；JD 正文技能信息集中在前部，截尾损失可控）
_JD_TEXT_MAX_CHARS = 20000


def _build_jd_text(snapshot: dict, raw_text: str) -> str:
    """拼装 JD 抽取正文。

    优先 snapshot 的干净文本字段（raw_text 为原始 HTML/JSON 备份，不适合直接喂 LLM）；
    但正文字段（description/requirements）缺失时拼接结果过短无法抽取，
    此时回退 raw_text（黄金集等数据正文可能只存在 raw_text 中）。
    统一裁剪至 _JD_TEXT_MAX_CHARS（入库不再截断，抽取输入侧兜底）。
    """
    body_fields = (snapshot.get("description"), snapshot.get("requirements"))
    if not any(str(f or "").strip() for f in body_fields):
        text = raw_text
    else:
        parts = [str(snapshot.get(f, "")).strip() for f in _JD_TEXT_FIELDS]
        text = "\n".join(p for p in parts if p)
    return text[:_JD_TEXT_MAX_CHARS]


def _is_jd_text_short(snapshot: dict, raw_text: str) -> bool:
    """JD 正文是否过短（<10 字符，无法抽取）。"""
    return len((_build_jd_text(snapshot, raw_text) or "").strip()) < 10


async def batch_extract(
    ctx: dict,
    jd_ids: list[int] | None = None,
    limit: int = 100,
) -> dict:
    """LLM 批量实体抽取 + JD 入图（M3 实现，依赖 AL-M3-01）。

    选取 jd_raw 中尚未抽取（snapshot 无 extraction 标记）的记录：
    - 拼装 JD 正文 → JDExtractor.extract_batch（N 条/批一次 LLM 调用，设计文档 §6.5
      批量抽取优化：批量输出 token 线性放大，走独立 batch_timeout；整批失败/条数
      错位时该批降级逐条 extract，单条失败不阻塞整体）
    - 抽取结果写回 jd_raw.snapshot["extraction"]（可审计、可重跑）
    - kg_service.import_jd 入图（Neo4j MERGE 幂等，重复执行不产生重复节点）

    全部失败时抛出，由 ARQ 重试机制兜底。
    """

    from app.core.database import async_session_factory, neo4j_driver
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

        # 过滤过短正文（<10 字符无法抽取）与低质 JD（needs_review 人工复核标记）：
        # 写 skipped 标记推进游标，否则 `extraction IS NULL` 游标永不推进
        # （短文本行/低质行堆积时正常 JD 饿死）
        valid: list[JDRaw] = []
        results: dict = {"processed": 0, "succeeded": 0, "failed": [], "positions": [], "skipped_dup": 0}
        for row in rows:
            snap = row.snapshot or {}
            if _is_jd_text_short(snap, row.raw_text or ""):
                snap = dict(snap)
                snap["extraction"] = {"skipped": True, "reason": "JD 正文过短（<10 字符）"}
                row.snapshot = snap
                results["failed"].append({"jd_id": row.id, "error": "JD 正文过短（<10 字符），跳过"})
            elif snap.get("needs_review"):
                # 低质 JD（爬虫端质量评分 < 0.6 标记）：跳过 LLM 抽取，
                # 写 skipped 标记推进游标，否则 `extraction IS NULL` 游标不推进
                snap = dict(snap)
                snap["extraction"] = {"skipped": True, "reason": "质量评分 < 0.6，需人工复核"}
                row.snapshot = snap
                results["failed"].append({"jd_id": row.id, "error": "质量评分 < 0.6，跳过"})
            else:
                valid.append(row)

        # 批量抽取：一次调用处理全部有效 JD——组批（batch_size 条数 + max_batch_chars
        # 文本总长双封顶）→ 每批一次 LLM 调用（独立 batch_timeout，设计文档 §6.5）→
        # 拆条落库。返回顺序与 valid 一一对应（错位/失败批次已降级逐条）。
        total = len(valid)
        texts = [_build_jd_text(r.snapshot or {}, r.raw_text or "") for r in valid]
        if texts:
            # 同步 LLM 批量调用放线程池，避免阻塞 ARQ 事件循环（Redis 心跳超时崩溃根因）。
            # concurrency=6 / batch_size=8：2026-08-07 用户确认提速（max_tokens 同步调至 4096）。
            # LLM 生成时间由输出 token 总量决定，并发提吞吐；若触发 provider 429，退避期
            # 整批降级逐条反而更慢，届时回调参数。
            extractions = await asyncio.to_thread(
                extractor.extract_batch,
                texts,
                batch_size=8,
                batch_timeout=180,  # 批量输出 token 放大，独立超时
                max_batch_chars=8000,
                concurrency=6,
            )
        else:
            extractions = []

        # 规范岗位名单独写入快照，保留原始抽取 position_name 供审计和评测。
        from app.services.extraction.dictionary import normalize_position_name

        normalized_positions = [
            normalize_position_name(
                extraction.position_name,
                skills=[s.name for s in (extraction.skills or [])],
            )
            for extraction in extractions
        ]

        for i, (row, extraction, normalized_position) in enumerate(
            zip(valid, extractions, normalized_positions), start=1
        ):
            # 逐条打印 jd_id + 进度百分比：batch_extract 只在循环结束 commit，
            # 中间进度 DB 不可见，靠此日志实时确认推进（worker.err.log）
            print(
                f"[batch_extract] 处理 jd_id={row.id}（{i}/{total}，{i / total * 100:.0f}%）",
                flush=True,
            )
            # SimHash 重复记录不入图（与 rebuild_graph/聚合口径一致）：重复内容
            # 已在 canonical 记录名下入图，此处再入会残留"聚合不覆盖"的空权
            # REQUIRES 边（08-15 核查：7 岗位/115 空权边根因，见 project_memory）。
            # 抽取结果仍落库——聚合/入图均已跳过该记录，落库仅为推进游标
            # （`extraction IS NULL` 条件）避免下次批跑重复调用 LLM。
            if (row.snapshot or {}).get("_duplicate_of"):
                snap = dict(row.snapshot or {})
                snap["extraction"] = extraction.model_dump()
                snap["normalized_position"] = normalized_position
                row.snapshot = snap
                results["skipped_dup"] += 1
                continue
            try:
                evidence = {
                    "source": row.source,
                    "source_url": row.source_url,
                    "crawled_at": row.crawled_at,
                    "raw_text": _build_jd_text(row.snapshot or {}, row.raw_text or ""),
                }
                with neo4j_driver.session() as neo4j_session:
                    # 同步 Neo4j 写入放线程池，避免阻塞事件循环
                    position_id = await asyncio.to_thread(
                        import_jd, neo4j_session, extraction, evidence
                    )
                # 入图成功后才写 extraction 标记：先标记后入图会让失败记录
                # extraction 落库，下次批跑 `extraction IS NULL` 不再选中，图数据永久缺失
                snap = dict(row.snapshot or {})
                snap["extraction"] = extraction.model_dump()
                snap["normalized_position"] = normalized_position
                row.snapshot = snap
                results["processed"] += 1
                results["succeeded"] += 1
                results["positions"].append({"jd_id": row.id, "position_id": position_id})
            except Exception as e:
                # 入图失败：不写 extraction（保持 IS NULL 下次批跑重试），
                # 错误写入 extraction_error 落库审计（failed 可追溯）
                snap = dict(row.snapshot or {})
                snap["extraction_error"] = str(e)[:500]
                row.snapshot = snap
                results["failed"].append({"jd_id": row.id, "error": str(e)[:500]})
        await session.commit()

    if results["processed"] > 0 and results["succeeded"] == 0:
        raise RuntimeError(f"批量抽取全部失败: {results['failed'][:5]}")
    return results


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

