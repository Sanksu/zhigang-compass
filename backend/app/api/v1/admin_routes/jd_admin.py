"""JD 原始数据管理（endpoint /admin/jd，RBAC admin only）。

管理页 CRUD jd_raw：分页列表（q 关键词 / source 过滤）、详情（正文全文 +
抽取摘要）、编辑（raw_text/source_url 等元数据）、删除。

编辑语义（契约 JdAdminUpdate 对应）：
- 仅更新请求体中显式给出的字段（snapshot.title/company/location 与
  raw_text/source_url/crawled_at 列）
- raw_text 或标题类字段变更时 content_hash 按抽取输入口径（etl_tasks.
  _build_jd_text 同源）重算——使重爬重抽链路把该行视为已变更内容
- 抽取快照 snapshot.extraction 不动（重抽需手动触发 ETL，编辑不自动跑 LLM）
- 图谱 Evidence 节点为独立备份，编辑/删除不联动（删除后证据链仍可追溯）
- 全部变更写 AuditLog（operator 取 current_user.sub，须 users.id UUID）
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_permission
from app.core.database import get_db
from app.core.errors import ERR_NOT_FOUND, ERR_VALIDATION
from app.models.business import AuditLog
from app.models.raw import JDRaw
from app.schemas.common import error, ok
from app.services.extraction.position_normalization import (
    normalized_position_from_snapshot,
)
from app.services.graph.portrait_evidence import jd_detail
# 与 etl_tasks._content_hash 同源（sha256(拼装后抽取正文)）；
# 单独引用该纯函数，编辑正文后指纹口径保持一致
from app.workers.etl_tasks import _build_jd_text

logger = logging.getLogger(__name__)

router = APIRouter()

_EDITABLE_SNAPSHOT_FIELDS = ("title", "company", "location")


async def _get_row(db: AsyncSession, jd_id: int) -> JDRaw | None:
    return (await db.execute(
        select(JDRaw).where(JDRaw.id == jd_id)
    )).scalar_one_or_none()


@router.get("/jd")
async def list_jd(
    q: str = Query(default="", max_length=200),
    source: str = Query(default="", max_length=50),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("admin:*")),
):
    """JD 原始数据分页列表（管理页表格）。"""
    conditions = []
    if q:
        like = f"%{q}%"
        conditions.append(or_(
            JDRaw.snapshot["title"].astext.ilike(like),
            JDRaw.raw_text.ilike(like),
        ))
    if source:
        conditions.append(JDRaw.source == source)

    count_stmt = select(func.count()).select_from(JDRaw)
    list_stmt = select(JDRaw)
    if conditions:
        count_stmt = count_stmt.where(*conditions)
        list_stmt = list_stmt.where(*conditions)
    total = (await db.execute(count_stmt)).scalar_one()
    rows = (await db.execute(
        list_stmt.order_by(JDRaw.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    )).scalars().all()

    items = []
    for row in rows:
        snap = row.snapshot or {}
        items.append({
            "id": row.id,
            "title": str(snap.get("title") or ""),
            "company": str(snap.get("company") or ""),
            "source": row.source or "",
            "source_id": row.source_id or "",
            "source_url": row.source_url or "",
            "crawled_at": row.crawled_at or "",
            "is_desensitized": bool(row.is_desensitized),
            "position": normalized_position_from_snapshot(snap),
            "text_length": len(row.raw_text or ""),
            "updated_at": row.updated_at.isoformat() if row.updated_at else "",
        })
    return ok(data={"total": total, "page": page, "size": size, "items": items})


@router.get("/jd/{jd_id}")
async def get_jd(
    jd_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("admin:*")),
):
    """JD 详情（正文全文 + 抽取摘要，供编辑回显）。"""
    row = await _get_row(db, jd_id)
    if row is None:
        return error(ERR_NOT_FOUND, "JD 不存在", http_status=404)
    return ok(data=jd_detail(row))


@router.put("/jd/{jd_id}")
async def update_jd(
    jd_id: int,
    body: dict[str, Any],
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("admin:*")),
):
    """编辑 JD 元数据/正文；content_hash 同步重算，写 AuditLog。"""
    allowed = set(_EDITABLE_SNAPSHOT_FIELDS) | {"raw_text", "source_url", "crawled_at"}
    unknown = set(body.keys()) - allowed
    if unknown:
        return error(ERR_VALIDATION, f"不可编辑字段: {sorted(unknown)}")

    row = await _get_row(db, jd_id)
    if row is None:
        return error(ERR_NOT_FOUND, "JD 不存在", http_status=404)

    changed: dict[str, Any] = {}
    snap = dict(row.snapshot or {})
    for field in _EDITABLE_SNAPSHOT_FIELDS:
        if field in body and body[field] != snap.get(field):
            snap[field] = str(body[field] or "")
            changed[field] = body[field]
    if "raw_text" in body and body["raw_text"] != (row.raw_text or ""):
        row.raw_text = str(body["raw_text"] or "")
        changed["raw_text"] = f"<{len(row.raw_text)} 字>"
    if "source_url" in body:
        row.source_url = str(body["source_url"] or "")
        changed["source_url"] = row.source_url
    if "crawled_at" in body:
        row.crawled_at = str(body["crawled_at"] or "")
        changed["crawled_at"] = row.crawled_at

    if not changed:
        return ok(data=jd_detail(row))

    row.snapshot = snap
    # 正文/标题变更 → 重算抽取输入指纹（重爬重抽链路按此判定内容已变更）
    if "raw_text" in changed or any(f in changed for f in _EDITABLE_SNAPSHOT_FIELDS):
        row.content_hash = hashlib.sha256(
            (_build_jd_text(snap, row.raw_text or "") or "").encode("utf-8")
        ).hexdigest()

    operator = current_user.get("sub") or current_user.get("user_id", "admin")
    db.add(AuditLog(
        user_id=operator,
        action="admin.jd.update",
        resource="jd_raw",
        resource_id=str(jd_id),
        detail={"changed_fields": sorted(changed.keys())},
    ))
    await db.commit()
    logger.info("JD %s 已编辑（%s）by %s", jd_id, sorted(changed.keys()), operator)
    return ok(data=jd_detail(row))


@router.delete("/jd/{jd_id}")
async def delete_jd(
    jd_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("admin:*")),
):
    """删除 jd_raw 行（写 AuditLog；图谱 Evidence 备份不联动删除）。"""
    row = await _get_row(db, jd_id)
    if row is None:
        return error(ERR_NOT_FOUND, "JD 不存在", http_status=404)

    operator = current_user.get("sub") or current_user.get("user_id", "admin")
    await db.delete(row)
    db.add(AuditLog(
        user_id=operator,
        action="admin.jd.delete",
        resource="jd_raw",
        resource_id=str(jd_id),
        detail={
            "source": row.source,
            "source_id": row.source_id,
            "title": str((row.snapshot or {}).get("title") or ""),
        },
    ))
    await db.commit()
    logger.info("JD %s 已删除（%s/%s）by %s", jd_id, row.source, row.source_id, operator)
    return ok(data={"deleted": True, "id": jd_id})
