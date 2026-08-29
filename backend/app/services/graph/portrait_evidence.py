"""岗位画像条目证据回溯（endpoint GET /graph/position/{id}/portrait-evidence）。

画像侧「薪资/经验/学历」条目（如 '1-1.3万 ×9' / '3年以上 ×122' / '本科 ×245'）
由 aggregation.build_aggregates 对 jd_raw 逐条聚合而来。本模块按同一行集、
同一排除口径反查支撑某条目的具体 JD，供前端画像侧栏展示证据列表与出处。

口径一致性（与 build_aggregates 镜像，改动须两边同步）：
- SimHash 近似重复（snapshot._duplicate_of）不参与
- 时滞/通胀降权 jd_weight == 0（归档）不参与
- 岗位级通胀排除（岗位内通胀占比 ≥30% 时通胀 JD 全剔除）不参与
- 平台级源降权不影响「是否参与」，仅影响技能边权重——证据列表仍展示
- 岗位归属：normalized_position_from_snapshot（共享读路径）匹配岗位名

纯 PG 查询（jd_raw.snapshot 内含抽取六维与归一化岗位名），不查图——
Evidence 节点无抽取值且与 jd_raw 无键关联；调用方先经图上 Position 节点
完成 id → name 解析与可见性过滤。
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.raw import JDRaw
from app.services.extraction.position_normalization import (
    normalized_position_from_snapshot,
)
from app.services.kg.aggregation import (
    _is_inflated,
    _jd_decay_weight,
    _min_experience_years,
    parse_salary_range,
)

_SNIPPET_LEN = 120


def _experience_label(snapshot: dict) -> str:
    """经验分布标签（与 aggregation 口径一致：仅正文明确年限的 JD 计数）。"""
    years = _min_experience_years(snapshot)
    return f"{years:g}年以上" if years is not None else ""


def _education_label(snapshot: dict) -> str:
    """学历要求标签（抽取六维 education.level）。"""
    ext = snapshot.get("extraction") or {}
    return str(((ext.get("education") or {}).get("level") or "").strip())


def _salary_label(snapshot: dict) -> str:
    """薪资档位标签（salary_range 原文，解析成功为前提）。"""
    ext = snapshot.get("extraction") or {}
    text = str(ext.get("salary_range") or "").strip()
    return text if text and parse_salary_range(text) else ""


def entry_label(snapshot: dict, dimension: str) -> str:
    """JD 在指定维度上的画像条目标签（空串=不落任何条目）。"""
    snapshot = snapshot or {}
    if dimension == "salary":
        return _salary_label(snapshot)
    if dimension == "experience":
        return _experience_label(snapshot)
    if dimension == "education":
        return _education_label(snapshot)
    return ""


async def load_position_jd_rows(db: AsyncSession, position_name: str) -> list:
    """按归一化岗位名拉取参与聚合的 jd_raw 行（排除口径与聚合一致）。

    SQL 先按持久化 normalized_position 预筛（NULL 一并取回，兼容旧快照
    重算路径），Python 侧再用 normalized_position_from_snapshot（共享读
    路径，不信旧值）精确归属——聚合对全量行遍历，此处按岗位收窄。
    """
    result = await db.execute(
        select(JDRaw)
        .where(
            or_(
                JDRaw.snapshot["normalized_position"].astext == position_name,
                JDRaw.snapshot["normalized_position"].astext.is_(None),
            )
        )
        .order_by(JDRaw.crawled_at.desc(), JDRaw.id.desc())
    )
    rows = result.scalars().all()

    matched = []
    for row in rows:
        snap = row.snapshot or {}
        if normalized_position_from_snapshot(snap) != position_name:
            continue
        if snap.get("_duplicate_of"):
            continue
        if _jd_decay_weight(snap) == 0:
            continue
        matched.append(row)

    # 岗位级通胀排除（§4.8）：岗位内通胀 JD 占比 ≥30% 时通胀 JD 全剔除
    pos_total = len(matched)
    pos_inflated = sum(1 for row in matched if _is_inflated(row.snapshot or {}))
    if pos_total > 0 and pos_inflated / pos_total >= 0.30:
        matched = [row for row in matched if not _is_inflated(row.snapshot or {})]
    return matched


def _item(row: JDRaw) -> dict:
    snap = row.snapshot or {}
    raw = row.raw_text or ""
    return {
        "jd_id": row.id,
        "title": str(snap.get("title") or ""),
        "company": str(snap.get("company") or ""),
        "source": row.source or "",
        "source_url": row.source_url or "",
        "crawled_at": row.crawled_at or "",
        "salary_text": _salary_label(snap),
        "experience_label": _experience_label(snap),
        "education_level": _education_label(snap),
        "snippet": raw[:_SNIPPET_LEN],
    }


async def portrait_evidence(
    db: AsyncSession,
    position_name: str,
    dimension: str,
    label: str,
    limit: int = 50,
) -> dict:
    """画像条目 → 证据 JD 列表（dimension+label 过滤，label 空取该维度全部）。"""
    rows = await load_position_jd_rows(db, position_name)
    items = []
    for row in rows:
        snap = row.snapshot or {}
        text = entry_label(snap, dimension)
        if not text:
            continue
        if label and text != label:
            continue
        items.append(_item(row))
    return {"rows": rows, "items": items}


def _experience_range_text(ext: dict) -> str:
    """抽取 experience_range 区间 → 展示文本（'3-5年' / '3年以上'）。"""
    rng = ext.get("experience_range") or {}
    if not isinstance(rng, dict):
        return ""
    lo, hi = rng.get("min_years"), rng.get("max_years")
    if lo is None and hi is None:
        return ""
    if hi is None:
        return f"{lo}年以上"
    if lo is None:
        return f"{hi}年以下"
    return f"{lo}-{hi}年"


def jd_detail(row: JDRaw) -> dict:
    """jd_raw 行 → 正文详情响应（查看侧 /graph/jd/{jd_id} 与管理侧共用字段）。"""
    snap = row.snapshot or {}
    ext = snap.get("extraction") or {}
    return {
        "id": row.id,
        "title": str(snap.get("title") or ""),
        "company": str(snap.get("company") or ""),
        "location": str(snap.get("location") or ""),
        "source": row.source or "",
        "source_id": row.source_id or "",
        "source_url": row.source_url or "",
        "crawled_at": row.crawled_at or "",
        "is_desensitized": bool(row.is_desensitized),
        "raw_text": row.raw_text or "",
        "position": normalized_position_from_snapshot(snap),
        "extraction_summary": {
            "salary_range": str(ext.get("salary_range") or ""),
            "education_level": _education_label(snap),
            "experience": _experience_range_text(ext),
        },
    }
