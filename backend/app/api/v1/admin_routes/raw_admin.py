"""课程/论文/社区信号原始数据管理（endpoint /admin/raw/{raw_type}，RBAC admin only）。

与 jd_admin 同骨架的通用 raw 表管理：分页列表（q 关键词 / source 过滤）、
详情、编辑（title/raw_text/source_url）、删除。覆盖 course_raw / paper_raw /
community_raw 三张表（jd_raw 保留专用 /admin/jd——含归一化岗位、复核放行、
抽取摘要等 JD 特有口径）。

编辑语义：
- 仅更新请求体中显式给出的字段（snapshot.title 与 raw_text/source_url 列）
- 不重算 content_hash：三张表无 JD 的重抽指纹链路；课程侧入图指纹在下次
  load_courses 时按新 snapshot 重算，编辑随既有 ETL 自然生效
- 图谱侧节点为独立备份，编辑/删除不联动
- 全部变更写 AuditLog（operator 取 current_user.sub，须 users.id UUID）
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import resolve_operator
from app.api.deps import require_permission
from app.core.database import get_db
from app.core.errors import ERR_NOT_FOUND
from app.models.business import AuditLog
from app.models.raw import CommunityRaw, CourseRaw, PaperRaw
from app.schemas.common import error, ok

logger = logging.getLogger(__name__)

router = APIRouter()

# 类型白名单 → ORM 模型（路径参数之外一律 404，防注入任意表名）
_RAW_TYPES: dict[str, type] = {
    "course": CourseRaw,
    "paper": PaperRaw,
    "community": CommunityRaw,
}


class RawAdminUpdateIn(BaseModel):
    """PUT /admin/raw/{raw_type}/{raw_id} 请求体（契约 RawAdminUpdate，强校验）。

    None=字段未提供（不更新）；空串=显式清空该字段。
    """

    title: str | None = Field(default=None, max_length=500)
    source_url: str | None = Field(default=None, max_length=2000)
    raw_text: str | None = Field(default=None, max_length=200_000)

    @field_validator("source_url")
    @classmethod
    def _validate_source_url(cls, v: str | None) -> str | None:
        """出处链接仅接受 http(s)（前端渲染 href，杜绝 javascript: 注入面）。"""
        if v and not re.match(r"^https?://", v):
            raise ValueError("source_url 仅支持 http(s) 链接")
        return v


def _resolve_model(raw_type: str):
    model = _RAW_TYPES.get(raw_type)
    if model is None:
        return None
    return model


def _extra_fields(raw_type: str, snap: dict) -> dict:
    """类型特有展示字段（列表列/详情摘要用；缺失键一律 None 不臆造）。"""
    if raw_type == "course":
        skills = snap.get("skills")
        return {
            "quality": snap.get("quality"),
            "institution": str(snap.get("institution") or ""),
            "skills_count": len(skills) if isinstance(skills, list) else None,
        }
    if raw_type == "paper":
        authors = snap.get("authors")
        return {
            "published": str(snap.get("published") or ""),
            "authors_count": len(authors) if isinstance(authors, list) else None,
        }
    if raw_type == "community":
        return {
            "stars": snap.get("stars"),
            "votes": snap.get("votes"),
            "trend_type": str(snap.get("trend_type") or ""),
        }
    return {}


async def _get_row(db: AsyncSession, model, raw_id: int):
    return (await db.execute(
        select(model).where(model.id == raw_id)
    )).scalar_one_or_none()


@router.get("/raw/{raw_type}")
async def list_raw(
    raw_type: str,
    q: str = Query(default="", max_length=200),
    source: str = Query(default="", max_length=50),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("admin:*")),
):
    """原始数据分页列表（管理页表格；q ILIKE 匹配 snapshot 标题与 raw_text）。"""
    model = _resolve_model(raw_type)
    if model is None:
        return error(ERR_NOT_FOUND, f"未知原始数据类型 {raw_type!r}", http_status=404)

    conditions = []
    if q:
        # LIKE 通配符转义（%/_ 按字面匹配，\ 为 ESCAPE 默认字符需先转义）
        literal = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{literal}%"
        conditions.append(or_(
            model.snapshot["title"].astext.ilike(like, escape="\\"),
            model.raw_text.ilike(like, escape="\\"),
        ))
    if source:
        conditions.append(model.source == source)

    count_stmt = select(func.count()).select_from(model)
    list_stmt = select(model)
    if conditions:
        count_stmt = count_stmt.where(*conditions)
        list_stmt = list_stmt.where(*conditions)
    total = (await db.execute(count_stmt)).scalar_one()
    rows = (await db.execute(
        list_stmt.order_by(model.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    )).scalars().all()

    items = []
    for row in rows:
        snap = row.snapshot or {}
        items.append({
            "id": row.id,
            "title": str(snap.get("title") or ""),
            "source": row.source or "",
            "source_id": row.source_id or "",
            "source_url": row.source_url or "",
            "crawled_at": row.crawled_at or "",
            "is_desensitized": bool(row.is_desensitized),
            "text_length": len(row.raw_text or ""),
            "updated_at": row.updated_at.isoformat() if row.updated_at else "",
            "extra": _extra_fields(raw_type, snap),
        })
    return ok(data={"total": total, "page": page, "size": size, "items": items})


@router.get("/raw/{raw_type}/{raw_id}")
async def get_raw(
    raw_type: str,
    raw_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("admin:*")),
):
    """原始数据详情（快照全量 + 正文全文，供编辑回显）。"""
    model = _resolve_model(raw_type)
    if model is None:
        return error(ERR_NOT_FOUND, f"未知原始数据类型 {raw_type!r}", http_status=404)
    row = await _get_row(db, model, raw_id)
    if row is None:
        return error(ERR_NOT_FOUND, "记录不存在", http_status=404)
    snap = row.snapshot or {}
    return ok(data={
        "id": row.id,
        "raw_type": raw_type,
        "title": str(snap.get("title") or ""),
        "source": row.source or "",
        "source_id": row.source_id or "",
        "source_url": row.source_url or "",
        "crawled_at": row.crawled_at or "",
        "is_desensitized": bool(row.is_desensitized),
        "raw_text": row.raw_text or "",
        "snapshot": snap,
    })


@router.put("/raw/{raw_type}/{raw_id}")
async def update_raw(
    raw_type: str,
    raw_id: int,
    body: RawAdminUpdateIn,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("admin:*")),
):
    """编辑原始数据（title/raw_text/source_url；写 AuditLog）。

    请求体经 RawAdminUpdateIn 强校验（None=不更新，空串=显式清空）。
    """
    body_map = {k: v for k, v in body.model_dump().items() if v is not None}

    operator, err = resolve_operator(current_user)
    if err is not None:
        return err
    model = _resolve_model(raw_type)
    if model is None:
        return error(ERR_NOT_FOUND, f"未知原始数据类型 {raw_type!r}", http_status=404)
    row = await _get_row(db, model, raw_id)
    if row is None:
        return error(ERR_NOT_FOUND, "记录不存在", http_status=404)

    changed: dict[str, str] = {}
    snap = dict(row.snapshot or {})
    if "title" in body_map and body_map["title"] != str(snap.get("title") or ""):
        snap["title"] = body_map["title"]
        changed["title"] = body_map["title"]
    if "raw_text" in body_map and body_map["raw_text"] != (row.raw_text or ""):
        row.raw_text = body_map["raw_text"]
        changed["raw_text"] = f"<{len(row.raw_text)} 字>"
    if "source_url" in body_map and body_map["source_url"] != (row.source_url or ""):
        row.source_url = body_map["source_url"]
        changed["source_url"] = body_map["source_url"]

    if not changed:
        return ok(data={"id": row.id, "raw_type": raw_type, "unchanged": True})

    row.snapshot = snap
    db.add(AuditLog(
        user_id=operator,
        action="admin.raw.update",
        resource=f"raw_{raw_type}",
        resource_id=str(raw_id),
        detail={"changed_fields": sorted(changed.keys())},
    ))
    await db.commit()
    logger.info("raw %s #%s 已编辑（%s）by %s", raw_type, raw_id, sorted(changed.keys()), operator)
    return ok(data={"id": row.id, "raw_type": raw_type, "changed_fields": sorted(changed.keys())})


@router.delete("/raw/{raw_type}/{raw_id}")
async def delete_raw(
    raw_type: str,
    raw_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("admin:*")),
):
    """删除原始数据行（写 AuditLog；图谱侧节点为独立备份不联动）。"""
    operator, err = resolve_operator(current_user)
    if err is not None:
        return err
    model = _resolve_model(raw_type)
    if model is None:
        return error(ERR_NOT_FOUND, f"未知原始数据类型 {raw_type!r}", http_status=404)
    row = await _get_row(db, model, raw_id)
    if row is None:
        return error(ERR_NOT_FOUND, "记录不存在", http_status=404)

    snap = row.snapshot or {}
    await db.delete(row)
    db.add(AuditLog(
        user_id=operator,
        action="admin.raw.delete",
        resource=f"raw_{raw_type}",
        resource_id=str(raw_id),
        detail={
            "source": row.source,
            "source_id": row.source_id,
            "title": str(snap.get("title") or ""),
        },
    ))
    await db.commit()
    logger.info("raw %s #%s 已删除（%s/%s）by %s", raw_type, raw_id, row.source, row.source_id, operator)
    return ok(data={"deleted": True, "id": raw_id, "raw_type": raw_type})
