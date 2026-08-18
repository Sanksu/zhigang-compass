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

from datetime import date, datetime
from typing import Iterable

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
