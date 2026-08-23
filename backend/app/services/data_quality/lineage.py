"""数据血缘溯源服务（P13：管理端可视化）。

将 jd_raw 已抽取记录按归一化岗位名分组，为每个岗位生成血缘详情
（LineageDetail）：

  - 组级跨源校验复用 cross_validate.validate_group（§4.5，与
    cross_validate_jds 任务同口径）——技能 ≥2 源印证、薪资异常、经验分歧、
    跨源置信度；
  - 血缘链明细（组内每条证据 JD：source / source_url / crawled_at / city /
    salary / skills / 是否 SimHash 去重标记）供管理端逐条追溯，定位原始
    招聘来源（图谱岗位/技能声明 ← 抽取证据 ← 原始 JD 记录）。

溯源语义：结果此前仅留存于 ETL 管线日志与 jd_raw 快照，管理端无法直观
查看；本服务把同一聚合口径的结果暴露为可查询的血缘视图。
"""

from app.services.data_quality.city_index import extract_city
from app.services.data_quality.cross_validate import build_position_groups, validate_group
from app.services.data_quality.schemas import LineageDetail, LineageRecordItem


def _record_item(rec: dict) -> LineageRecordItem:
    """单条 jd_raw 记录 → 血缘链明细项（溯源到原始来源）。"""
    snap = rec.get("snapshot") or {}
    ext = snap.get("extraction") or {}
    reqs = ext.get("requirements") or []
    if reqs:
        skills = [r.get("skill_name", "") for r in reqs if r.get("skill_name")]
    else:
        skills = [s.get("name", "") for s in (ext.get("skills") or []) if s.get("name")]
    return LineageRecordItem(
        jd_id=rec["id"],
        source=rec.get("source") or "",
        source_url=snap.get("source_url") or rec.get("source_url") or "",
        crawled_at=rec.get("crawled_at") or "",
        city=extract_city(snap.get("location")),
        salary=ext.get("salary_range") or snap.get("salary") or "",
        skills=skills,
        is_duplicate=bool(snap.get("_duplicate_of")),
    )


def build_lineage(records: list[dict]) -> list[LineageDetail]:
    """对已抽取记录生成按岗位分组的血缘详情列表（按岗位名排序）。

    Args:
        records: jd_raw 记录 dict 列表，每项须含
            {id, source, source_url, crawled_at, snapshot}。
    """
    details: list[LineageDetail] = []
    for pos, group in build_position_groups(records).items():
        result = validate_group(pos, group)
        details.append(
            LineageDetail(
                position_name=result.position_name,
                jd_count=result.jd_count,
                source_count=result.source_count,
                sources=result.sources,
                cities=result.cities,
                verified=result.verified,
                confidence=result.confidence,
                verified_skill_ratio=result.verified_skill_ratio,
                unverified_skills=result.unverified_skills,
                salary_median=result.salary_median,
                salary_outlier=result.salary_outlier,
                experience_divergence=result.experience_divergence,
                records=[_record_item(rec) for rec in group],
            )
        )
    return sorted(details, key=lambda d: d.position_name)


def lineage_summary(details: list[LineageDetail]) -> dict:
    """血缘总览汇总：分组数 / 覆盖 JD 数 / 多源印证 / 已验证 / 低置信。"""
    return {
        "groups": len(details),
        "jd_count": sum(d.jd_count for d in details),
        "multi_source": sum(1 for d in details if d.source_count >= 2),
        "verified": sum(1 for d in details if d.verified),
        "below_confidence": sum(1 for d in details if d.confidence < 0.6),
    }
