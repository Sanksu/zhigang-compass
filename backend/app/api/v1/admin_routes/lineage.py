"""管理后台数据血缘路由（RBAC admin only）。

对齐契约 /api/v1/admin/lineage/positions：
  - 列表：跨源交叉验证汇总 + 血缘总览统计（summary）
  - 详情：单个岗位的血缘链明细（证据 JD，溯源到原始来源）

数据口径与 ETL cross_validate_jds 一致（jd_raw 已抽取记录按归一化岗位名
分组），把此前仅留存于管线日志/快照的溯源结果暴露为管理端可视化视图。
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import ok
from app.core.database import get_db
from app.models.raw import JDRaw
from app.services.data_quality.lineage import build_lineage, lineage_summary

router = APIRouter()


def _load_records(rows) -> list[dict]:
    """JDRaw 行 → 血缘服务输入 dict（仅取溯源所需字段）。"""
    return [
        {
            "id": r.id,
            "source": r.source,
            "source_url": r.source_url,
            "crawled_at": r.crawled_at,
            "snapshot": r.snapshot or {},
        }
        for r in rows
    ]


async def _all_lineage(db: AsyncSession) -> list:
    """加载已抽取记录并生成全量血缘详情（CPU 分组校验放线程池）。"""
    rows = (
        await db.scalars(
            select(JDRaw)
            .where(JDRaw.snapshot["extraction"].astext.isnot(None))
            .order_by(JDRaw.id.asc())
        )
    ).all()
    return await asyncio.to_thread(build_lineage, _load_records(rows))


@router.get("/lineage/positions")
async def lineage_positions(
    q: str | None = Query(default=None, description="按岗位名关键字过滤"),
    verified: bool | None = Query(default=None, description="仅 ≥2 源印证已验证"),
    below_confidence: bool | None = Query(default=None, description="仅低置信（<0.6）"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """数据血缘岗位列表（跨源校验汇总 + 血缘总览统计）。"""
    details = await _all_lineage(db)
    if q:
        details = [d for d in details if q in d.position_name]
    if verified is not None:
        details = [d for d in details if d.verified is verified]
    if below_confidence is not None:
        details = [d for d in details if (d.confidence < 0.6) is below_confidence]

    total = len(details)
    start = (page - 1) * size
    items = [d.model_dump(exclude={"records"}) for d in details[start : start + size]]
    return ok(
        data={
            "items": items,
            "total": total,
            "page": page,
            "size": size,
            "summary": lineage_summary(details),
        }
    )


@router.get("/lineage/positions/{position_name}")
async def lineage_position_detail(
    position_name: str,
    db: AsyncSession = Depends(get_db),
):
    """单个岗位的血缘详情（组级校验 + 证据 JD 血缘链明细）。"""
    details = await _all_lineage(db)
    for detail in details:
        if detail.position_name == position_name:
            return ok(data=detail.model_dump())
    raise HTTPException(status_code=404, detail=f"岗位不存在: {position_name}")
