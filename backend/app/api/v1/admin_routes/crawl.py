"""管理后台爬虫域路由：状态监控 / 手动触发 / SSE 实时日志 / 历史（RBAC admin only）。

对齐契约 /api/v1/admin/crawl/*。平台计数以 raw 表为准，output 文件数为参考；
SSE 复用 app.api.common.sse_task_events 骨架 + Redis LIST 增量日志。
"""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import iso, paged_ok, paginate, serialize_task, sse_task_events
from app.core.arq_client import enqueue
from app.core.database import get_db
from app.core.errors import ERR_INTERNAL, ERR_VALIDATION
from app.models.business import TaskStatus
from app.models.raw import CommunityRaw, CourseRaw, JDRaw, PaperRaw
from app.schemas.admin_requests import CrawlTriggerRequest
from app.schemas.common import error, ok

router = APIRouter()
logger = logging.getLogger(__name__)

# 爬虫平台元信息（对齐前端 13 源展示，拉勾网已移除）
PLATFORM_META: dict[str, dict] = {
    "boss": {"name": "BOSS直聘", "level": "A"},
    "zhilian": {"name": "智联招聘", "level": "A"},
    "monster": {"name": "Monster", "level": "A"},
    "indeed": {"name": "Indeed", "level": "B"},
    "glassdoor": {"name": "Glassdoor", "level": "B"},
    "linkedin": {"name": "LinkedIn", "level": "C"},
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

# ============================================================
# 爬取管理（BE-M4-05）：手动触发爬取任务
# ============================================================

async def _enqueue_crawl(
    spider: str,
    keywords: list[str],
    cities: list[str] | None = None,
    task_id: str | None = None,
) -> None:
    """入队 ARQ crawl_platform 任务；队列不可用抛异常由调用方标记 failed。"""
    logger.info(
        f"[_enqueue_crawl] 准备入队: task_id={task_id} spider={spider} "
        f"keywords={keywords} cities={cities or '(默认)'}"
    )
    # task_id 供 crawl_platform 实时写日志队列 + 更新任务状态（SSE 端点消费）
    kwargs = {"spider_name": spider, "keywords": keywords, "task_id": task_id}
    if cities:
        kwargs["cities"] = cities
    await enqueue("crawl_platform", **kwargs)
    logger.info(f"[_enqueue_crawl] 入队成功: task_id={task_id} job=crawl_platform kwargs={kwargs}")


@router.post("/crawl/trigger", status_code=202)
async def crawl_trigger(req: CrawlTriggerRequest, db: AsyncSession = Depends(get_db)):
    """触发爬取任务（BE-M4-05，契约 /admin/crawl/trigger）。

    校验平台（PLATFORM_META 白名单）→ 建 TaskStatus(pending) → 入队 ARQ
    crawl_platform → 返回 task_id。队列不可用时标记任务 failed 并返回 500。
    keyword 留空 = 采集平台热度/最新内容（08-16 用户决策）。
    """
    platform = req.platform.strip()
    keyword = req.keyword.strip()
    city = req.city.strip()
    logger.info(f"[crawl/trigger] 收到触发请求: platform={platform} keyword={keyword or '(空=热度/最新)'} city={city or '(默认)'}")
    if platform not in _PLATFORM_TO_SPIDER:
        logger.warning(f"[crawl/trigger] 未知平台: {platform}")
        return error(ERR_VALIDATION, f"未知平台: {platform}（可选: {', '.join(sorted(PLATFORM_META))}）")

    try:
        task = TaskStatus(
            task_type="crawl",
            status="pending",
            result={"platform": platform, "keyword": keyword, "city": city or None},
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
    except Exception as e:
        logger.exception(
            f"[crawl/trigger] 任务落库失败: platform={platform} keyword={keyword} city={city or '(默认)'} err={e}"
        )
        # 08-14 审查：异常详情仅入服务端日志，不随响应外泄（错误详情泄露漏网点）
        return error(ERR_INTERNAL, "爬取任务落库失败，请稍后重试")
    logger.info(f"[crawl/trigger] 任务已建: task_id={task.id} platform={platform} keyword={keyword} city={city or '(默认)'}")

    try:
        await _enqueue_crawl(
            _PLATFORM_TO_SPIDER[platform],
            [keyword] if keyword else [],  # 空关键词 = 平台热度/最新采集（08-16 用户决策）
            cities=[city] if city else None,
            task_id=str(task.id),
        )
        logger.info(f"[crawl/trigger] 任务入队成功: task_id={task.id} spider={_PLATFORM_TO_SPIDER[platform]}")
    except Exception as e:
        task.status = "failed"
        task.error = "任务入队失败"  # 固定文案：详情仅入日志，防经 /crawl/history 透传内部信息
        await db.commit()
        logger.error(f"[crawl/trigger] 任务入队失败: task_id={task.id} err={e}")
        return error(ERR_INTERNAL, "爬取任务入队失败，请稍后重试")

    return ok(data={"task_id": task.id, "platform": platform, "status": "pending"})


# ============================================================
# 爬虫实时日志（SSE）：手动触发后逐行推送 scrapy 终端输出
# ============================================================

async def _crawl_log_events(
    task_uuid: str,
    get_logs,
    get_task,
    *,
    poll_interval: float = 0.5,
    timeout: float = 600.0,
):
    """爬虫实时日志 SSE 事件序列（可注入日志/任务查询函数便于测试）。

    事件流：log（每行 scrapy 输出）→ progress（周期心跳，含任务状态）→
    终态 success 推送 done、failed 推送 error 后关闭；任务不存在/超时推送
    error 后关闭。日志按 offset 增量拉取，避免重复推送。
    """
    offset = 0

    async def _poll_logs() -> list[str]:
        nonlocal offset
        try:
            lines = await get_logs(task_uuid, offset)
        except Exception:
            lines = []
        offset += len(lines)
        return [
            f"event: log\ndata: {json.dumps({'line': ln}, ensure_ascii=False)}\n\n"
            for ln in lines
        ]

    def _progress(task) -> dict:
        return {"status": task["status"], "progress": task["progress"]}

    async for event in sse_task_events(
        task_uuid,
        get_task,
        before_poll=_poll_logs,
        progress_payload=_progress,
        poll_interval=poll_interval,
        timeout=timeout,
    ):
        yield event


@router.get("/crawl/task/{task_id}/stream")
async def crawl_task_stream(task_id: str):
    """SSE 实时推送爬虫终端日志（手动触发场景，BE-M4-05 扩展）。

    日志来源为 crawl_platform 逐行写入 Redis 的 LIST（crawl:log:{task_id}，
    TTL 1h），按 offset 增量拉取；任务状态由 TaskStatus 驱动终态
    （success → done / failed → error）。任务不存在 / 推送超时（600s）结束。
    """
    try:
        task_uuid = str(uuid.UUID(task_id))
    except (ValueError, AttributeError):
        return error(ERR_VALIDATION, "task_id 格式非法")

    from app.core.arq_client import get_pool

    async def _get_task(tid: str) -> dict | None:
        from app.core.database import async_session_factory
        from app.models.business import TaskStatus

        async with async_session_factory() as session:
            task = await session.get(TaskStatus, tid)
        return serialize_task(task) if task is not None else None

    async def _get_logs(tid: str, start: int) -> list[str]:
        # 复用模块级 ARQ 连接池（08-14 审查：此前每 0.5s 新建池，600s 轮询 ≈ 1200 次建连）
        pool = await get_pool()
        raw = await pool.lrange(f"crawl:log:{tid}", start, -1)
        return [ln.decode("utf-8", errors="replace") if isinstance(ln, bytes) else str(ln) for ln in raw]

    async def _event_gen():
        async for event in _crawl_log_events(task_uuid, _get_logs, _get_task):
            yield event

    return StreamingResponse(_event_gen(), media_type="text/event-stream")


@router.get("/crawl/history")
async def crawl_history(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """爬取历史（BE-M4-05 扩展）：task_status 中 crawl 任务列表，倒序分页。

    字段来源 task.result（触发时写 platform/keyword，crawl_platform 合并写入
    spider/output_file/items），status 为 pending/running/success/failed。
    """
    stmt = select(TaskStatus).where(TaskStatus.task_type == "crawl")
    count_stmt = (
        select(func.count()).select_from(TaskStatus).where(TaskStatus.task_type == "crawl")
    )
    rows, total = await paginate(
        db, stmt.order_by(TaskStatus.created_at.desc()), page, size, count_stmt=count_stmt
    )
    return paged_ok([_history_row(t) for t in rows], total, page, size)

