"""ETL pipeline stage tasks and shared JD snapshot helpers.

Ownership: the ETL pipeline stages run by ``app.workers.etl.run_etl_pipeline``
(validate_temporal / detect_inflation / dedup_simhash / batch_extract /
aggregate_positions / cross_validate_jds / sync_skill_normalization /
backfill_embeddings / snapshot_graph) plus the JD snapshot/date helper
functions they share.

ARQ registration constraint: the facade ``app.workers.tasks`` re-exports every
name defined here, and ``app.workers.settings.WorkerSettings.functions`` keeps
importing tasks from that facade — function names (``__qualname__``) must stay
identical to preserve ARQ job matching.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import select

from app.models.raw import JDRaw

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
    # 空格分隔时间格式（智联等源，占库内 46%）：缺此格式会导致时滞检测
    # 把合法日期误标 no_skills_or_publish_date 跳过
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _skill_first_seen_days(
    group: list[tuple[int, date, list[str]]],
    skills: list[str],
    today: date,
    graph_first_seen: dict[str, date] | None = None,
) -> list[int]:
    """技能首见时长（天）。

    优先读图谱 Skill.first_seen（全局首次入图时间，G-02 主口径）；图谱无
    该技能首见记录（存量节点无属性/未入图）时回退同岗位 jd_raw 最早出现
    日期近似。group: 同岗位已抽取记录 (jd_id, publish_date, skills)。
    某技能两种来源均无记录时不计入（数据不足不武断判定）。
    """
    from app.services.extraction.post_processor import canonical_skill_name

    ages = []
    for skill in skills:
        first = None
        if graph_first_seen:
            first = graph_first_seen.get(canonical_skill_name(skill))
        if first is None:
            for _, pdate, group_skills in group:
                if skill in group_skills and (first is None or pdate < first):
                    first = pdate
        if first is not None:
            ages.append(max(0, (today - first).days))
    return ages


def _graph_skill_first_seen(skills: Iterable[str]) -> dict[str, date]:
    """从图谱读 Skill.first_seen（首次入图时间，G-02）→ 归一化技能名 → 日期。

    技能节点按归一化名存储（canonical_skill_name），未归一化的原始名查询
    会 miss，故先归一化再匹配；无 first_seen 的存量节点跳过（回退 jd_raw）。
    图谱不可达时返回空 dict（回退 jd_raw 推算）：validate_temporal 原本为
    纯 PG 依赖，不因本次加读图而引入 Neo4j 强依赖（backfill 脚本可独立运行）。
    """
    import logging

    from app.core.database import neo4j_driver
    from app.services.extraction.post_processor import canonical_skill_name

    logger = logging.getLogger(__name__)
    names = {canonical_skill_name(s) for s in skills if canonical_skill_name(s)}
    if not names:
        logger.info("_graph_skill_first_seen: 无有效技能名，跳过读图（空映射，回退 jd_raw）")
        return {}
    logger.info(
        "_graph_skill_first_seen: 技能请求=%d 归一化去重后=%d",
        len(skills), len(names),
    )
    try:
        with neo4j_driver.session() as session:
            rows = session.run(
                "MATCH (s:Skill) WHERE s.name IN $names "
                "RETURN s.name AS name, s.first_seen AS first_seen",
                names=list(names),
            ).data()
    except Exception as exc:
        # 图谱不可达（懒连接失败/服务停止）不阻断时滞检测，回退 jd_raw 推算
        logger.warning(
            "_graph_skill_first_seen: 图谱不可达，回退 jd_raw 推算: %s: %s",
            type(exc).__name__, exc,
        )
        return {}
    out: dict[str, date] = {}
    parse_failed: list[str] = []
    for r in rows:
        raw = r.get("first_seen")
        if not raw:
            continue
        try:
            out[r["name"]] = datetime.fromisoformat(str(raw)).date()
        except ValueError:
            parse_failed.append(r["name"])
    missing = sorted(names - set(out))
    logger.info(
        "_graph_skill_first_seen: 图谱命中=%d/%d%s",
        len(out), len(names),
        "" if not missing else f"，缺失 {len(missing)} 个将回退 jd_raw: {missing[:10]}"
        + ("" if len(missing) <= 10 else f" 等共 {len(missing)} 个"),
    )
    if parse_failed:
        logger.warning(
            "_graph_skill_first_seen: %d 个技能 first_seen 解析失败被跳过（回退 jd_raw）: %s",
            len(parse_failed), parse_failed[:10],
        )
    return out


def _experience_years(snapshot: dict) -> int | None:
    """解析经验要求最小年限（如 "3-5年" → 3）；无法解析返回 None。"""
    import re

    m = re.search(r"(\d+)", str(snapshot.get("experience") or ""))
    return int(m.group(1)) if m else None


def _history_skill_sets(group: list[tuple[int, date, list[str]]], jd_id: int) -> list[set[str]]:
    """同岗位历史 JD 的技能集合（按发布时间升序），排除当前 JD 自身。

    僵尸 JD 判定依赖"连续 N 期技能几乎不变"，与当前技能完全相同的历期
    （Jaccard=1.0）是最强信号，必须保留参与相似度计数；仅排除当前 JD 自身
    （原实现 `if gs != skills` 误排除了完全相同技能的历期，
    导致 detect_zombie_jd 的连续周期永远数不足 4 期，僵尸检测失效）。
    """
    return [
        set(gs)
        for r_id, pdate, gs in sorted(group, key=lambda g: g[1])
        if r_id != jd_id
    ]


def _snapshot_with_skip(snapshot: dict | None, key: str, reason: str) -> dict:
    """复制 snapshot 并写入检测跳过标记（数据不足，游标收敛用，不做判定）。

    时滞/通胀检测对数据不足的 JD 不做武断判定，但若不写标记，
    `snapshot[key] is None` 游标会反复选中这些 JD，每次 ETL 空转。
    skipped 标记不含 decay_weight，聚合层 `_jd_decay_weight` 视为 1.0。
    """
    snap = dict(snapshot or {})
    snap[key] = {"skipped": True, "reason": reason}
    return snap


async def validate_temporal(
    ctx: dict,
    jd_ids: list[int] | None = None,
    limit: int = 200,
) -> dict:
    """时滞检测（设计文档 §4.7）：jd_raw 已抽取记录接入 SAI/僵尸/抄袭检测。

    技能首见时长优先读图谱 Skill.first_seen（G-02，全局首次入图时间），
    图谱缺失时回退同岗位 jd_raw 历史最早出现日期近似。
    检测结果写回 `snapshot["validation"]`（含三类结果 + 叠加降权系数）；
    数据不足（无技能/无发布日期）的 JD 跳过，不做武断判定。
    """

    from app.core.database import async_session_factory
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
        stmt = select(JDRaw).where(
            JDRaw.snapshot["extraction"].astext.isnot(None),
            # 游标：仅处理未做时滞检测的记录（幂等，重复执行不空转旧数据）
            JDRaw.snapshot["validation"].astext.is_(None),
        )
        if jd_ids:
            stmt = stmt.where(JDRaw.id.in_(jd_ids))
        rows = (await session.scalars(stmt.order_by(JDRaw.id.asc()).limit(limit))).all()

        results: dict = {"checked": 0, "skipped": 0, "flagged": []}
        # 已抽取记录视图：(jd_id, position, publish_date, skills)
        views = []
        for row in rows:
            ext = _extraction_of(row)
            if not ext:
                results["skipped"] += 1
                continue
            publish = _publish_date(row.snapshot or {}, row.crawled_at or "")
            skills = _skills_of(ext)
            if not skills or publish is None:
                row.snapshot = _snapshot_with_skip(row.snapshot, "validation", "no_skills_or_publish_date")
                results["skipped"] += 1
                continue
            views.append((row, (row.id, ext.get("position_name") or "", publish, skills)))

        # 历史组补齐：时滞检测的技能首见时长/抄袭比对需要同岗位全量历史
        # （含此前已验证批次）。仅用本次未验证的 limit 条记录，首见时长会被
        # 低估、抄袭比对缺参照（审查 major：validate_temporal 历史组不齐）。
        position_names = {v[1][1] for v in views}
        hist_by_pos: dict[str, list[tuple[int, date, list[str]]]] = {}
        if position_names:
            hist = (await session.scalars(
                select(JDRaw).where(
                    JDRaw.snapshot["extraction"].astext.isnot(None),
                    JDRaw.snapshot["extraction"]["position_name"].astext.in_(position_names),
                )
            )).all()
            for row in hist:
                ext = _extraction_of(row)
                if not ext:
                    continue
                publish = _publish_date(row.snapshot or {}, row.crawled_at or "")
                skills = _skills_of(ext)
                if not skills or publish is None:
                    continue
                pos = ext.get("position_name") or ""
                hist_by_pos.setdefault(pos, []).append((row.id, publish, skills))

        # 图谱 Skill.first_seen 一次性读取（G-02 主口径）：当前批次 + 历史组
        # 全部技能名批量查询，避免逐技能 N+1 查询
        all_skills: set[str] = set()
        for _, (_, _, _, v_skills) in views:
            all_skills.update(v_skills)
        for grp in hist_by_pos.values():
            for _, _, gs in grp:
                all_skills.update(gs)
        graph_first_seen = _graph_skill_first_seen(all_skills) if all_skills else {}

        for row, (jd_id, position, publish, skills) in views:
            # group 覆盖同岗位全部历史记录（含当前批次），按首见时长/抄袭比对口径
            group = hist_by_pos.get(position, [])
            skill_ages = _skill_first_seen_days(group, skills, today, graph_first_seen)
            if not skill_ages:
                row.snapshot = _snapshot_with_skip(row.snapshot, "validation", "no_skill_first_seen_ages")
                results["skipped"] += 1
                continue

            # 同岗位近 90 天窗口的技能首见时长聚合，作为 SAI 基线
            recent_ages = [
                age
                for _, pdate, gs in group
                if (today - pdate).days <= RECENT_WINDOW_DAYS
                for age in _skill_first_seen_days(group, gs, today, graph_first_seen)
            ]
            sai = classify_sai(compute_sai(skill_ages, recent_ages))

            history_skills = _history_skill_sets(group, jd_id)
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

    from app.core.database import async_session_factory
    from app.services.data_quality.inflation_detector import compute_inflation_score

    async with async_session_factory() as session:
        stmt = select(JDRaw).where(
            JDRaw.snapshot["extraction"].astext.isnot(None),
            # 游标：仅处理未做通胀检测的记录（幂等，重复执行不空转旧数据）
            JDRaw.snapshot["inflation"].astext.is_(None),
        )
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
                row.snapshot = _snapshot_with_skip(row.snapshot, "inflation", "no_level")
                results["skipped"] += 1
                continue
            min_years = _experience_years(row.snapshot or {})
            if min_years is None:
                row.snapshot = _snapshot_with_skip(row.snapshot, "inflation", "no_experience")
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
