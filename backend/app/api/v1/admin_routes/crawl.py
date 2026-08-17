"""管理后台爬虫域路由：状态监控 / 手动触发 / SSE 实时日志 / 历史（RBAC admin only）。

对齐契约 /api/v1/admin/crawl/*。平台计数以 raw 表为准，output 文件数为参考；
SSE 复用 app.api.common.sse_task_events 骨架 + Redis LIST 增量日志。
"""

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import iso
from app.core.database import get_db
from app.models.business import TaskStatus
from app.models.raw import CommunityRaw, CourseRaw, JDRaw, PaperRaw
from app.schemas.common import ok

router = APIRouter()
logger = logging.getLogger(__name__)

# 爬虫平台元信息（对齐前端 13 源展示，拉勾网已移除）
PLATFORM_META: dict[str, dict] = {
    "boss": {"name": "BOSS直聘", "level": "A"},
    "zhilian": {"name": "智联招聘", "level": "A"},
    "monster": {"name": "Monster", "level": "A"},
    "indeed": {"name": "Indeed", "level": "A"},
    "glassdoor": {"name": "Glassdoor", "level": "B"},
    "linkedin": {"name": "LinkedIn", "level": "B"},
    "maimai": {"name": "脉脉", "level": "C"},
    "github": {"name": "GitHub", "level": "信号"},
    "stackoverflow": {"name": "Stack Overflow", "level": "信号"},
    "arxiv": {"name": "arXiv", "level": "论文"},
    "icourse163": {"name": "中国大学MOOC", "level": "课程"},
    "coursera": {"name": "Coursera", "level": "课程"},
    "edx": {"name": "edX", "level": "课程"},
}

_OUTPUT_DIR = Path(__file__).resolve().parents[4] / "data" / "crawlers" / "output"

# raw 表 source（spider 名）→ 平台 id（linkedin_public 对应 linkedin）
_SPIDER_TO_PLATFORM = {**{p: p for p in PLATFORM_META}, "linkedin_public": "linkedin"}


# ============================================================
# 爬虫状态
# ============================================================

def _match_platform(stem: str) -> str | None:
    """从 output 文件名解析平台：{platform}.jsonl 或 {platform}_{YYYYMMDD}_{HHMMSS}.jsonl。"""
    for pid in PLATFORM_META:
        if stem == pid or stem.startswith(pid + "_"):
            return pid
    return None

@router.get("/crawl/status")
async def crawl_status(db: AsyncSession = Depends(get_db)):
    """爬取状态监控：raw 表实际入库统计（每源条数/今日/最近）+ output 文件数。

    平台计数以 raw 表为准（output JSONL 可能被清理/未保留，入库数据才是最终状态）；
    files 为 output/*.jsonl 文件数，last_run 取 raw 最近入库与 task_status 触发时间较新者。
    """
    today_cst_start = (
        datetime.now(timezone(timedelta(hours=8)))
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .astimezone(timezone.utc)
    )
    # 各 raw 表按 source（spider 名）统计：总条数 / 今日（CST）新增 / 最近入库
    raw_stats: dict[str, dict] = {}
    for model in (JDRaw, CourseRaw, PaperRaw, CommunityRaw):
        rows = await db.execute(
            select(
                model.source,
                func.count(),
                func.count().filter(model.created_at >= today_cst_start),
                func.max(model.created_at),
            ).group_by(model.source)
        )
        for source, total, today, last_ts in rows.all():
            pid = _SPIDER_TO_PLATFORM.get(source, source)
            entry = raw_stats.setdefault(pid, {"total": 0, "today": 0, "last": None})
            entry["total"] += total or 0
            entry["today"] += today or 0
            if last_ts and (entry["last"] is None or last_ts > entry["last"]):
                entry["last"] = last_ts

    # 各平台最后触发时间（task_status 记录，含失败；spider 名归一化为平台 id）
    task_last_run: dict[str, str] = {}
    crawl_tasks = await db.scalars(
        select(TaskStatus).where(TaskStatus.task_type == "crawl")
    )
    for t in crawl_tasks:
        spider = (t.result or {}).get("spider")
        if not spider:
            continue
        pid = _SPIDER_TO_PLATFORM.get(spider, spider)
        ts = iso(t.created_at)
        if ts and (pid not in task_last_run or ts > task_last_run[pid]):
            task_last_run[pid] = ts

    # 平台 → output/*.jsonl 文件数（platforms[].files 字段，管理参考用）
    file_counts: dict[str, int] = {}
    if _OUTPUT_DIR.exists():
        for f in sorted(_OUTPUT_DIR.glob("*.jsonl")):
            platform = _match_platform(f.stem)
            if platform is not None:
                file_counts[platform] = file_counts.get(platform, 0) + 1

    # 全量平台聚合（13 源）：raw 入库为准，无记录平台也列出（前端展示"归档"状态）
    platforms = []
    for pid, meta in PLATFORM_META.items():
        st = raw_stats.get(pid, {"total": 0, "today": 0, "last": None})
        last_run = iso(st["last"])
        ts = task_last_run.get(pid)
        if ts and (last_run is None or ts > last_run):
            last_run = ts
        platforms.append({
            "id": pid,
            "name": meta["name"],
            "level": meta["level"],
            "files": file_counts.get(pid, 0),
            "total_count": st["total"],
            "today_count": st["today"],
            "last_run": last_run,
        })

    raw_counts = {
        "jd": await db.scalar(select(func.count()).select_from(JDRaw)) or 0,
        "course": await db.scalar(select(func.count()).select_from(CourseRaw)) or 0,
        "paper": await db.scalar(select(func.count()).select_from(PaperRaw)) or 0,
        "community": await db.scalar(select(func.count()).select_from(CommunityRaw)) or 0,
    }
    return ok(data={
        "metrics": {
            "today_count": sum(s["today"] for s in raw_stats.values()),  # 今日（CST）入库新增
            # 累计采集量统一 DB 口径（08-15 用户决策）：与仪表盘一致的四表入库
            # 总量；output jsonl 行数口径废弃（output 含未入库/重复记录，易误导）
            "raw_total": raw_counts["jd"] + raw_counts["course"] + raw_counts["paper"] + raw_counts["community"],
            "raw": raw_counts,
        },
        "platforms": sorted(platforms, key=lambda x: (x["level"], x["id"])),
    })


# 手动触发映射（crawl_platform 消费）

# 平台 ID（前端 PLATFORM_META 口径）→ Scrapy spider 名（crawl_platform 消费）
_PLATFORM_TO_SPIDER = {
    **{p: p for p in PLATFORM_META},
    "linkedin": "linkedin_public",
}


# crawl 任务 → 历史行

def _history_row(task) -> dict:
    """crawl 任务 → 历史行（platform 取 spider 名回退触发时 platform，映射中文名）。"""
    result = task.result or {}
    spider = result.get("spider") or result.get("platform") or ""
    return {
        "id": task.id,
        "platform": spider,
        "platform_name": PLATFORM_META.get(spider, {}).get("name", spider or "—"),
        "keyword": result.get("keyword") or "",
        "status": task.status,
        "items": result.get("items") or 0,
        "error": task.error or "",
        "created_at": iso(task.created_at),
    }
