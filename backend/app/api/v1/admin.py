"""管理后台路由：用户管理 / 审计日志 / 爬虫状态 / 岗位审核（RBAC admin only）。

对齐契约 /api/v1/admin/*。岗位审核（positions/pending）依赖 LLM 抽取信号，
当前返回空列表（M3/M4 交付后接入真实数据）。
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import asyncio
import json
import re
import time
import uuid

import yaml
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_permission
from app.core.config import settings
from app.core.database import get_db
from app.core.security import hash_password
from app.models.business import AuditLog, TaskStatus, User
from app.models.raw import CommunityRaw, CourseRaw, JDRaw, PaperRaw
from app.schemas.common import ok, error

router = APIRouter(prefix="/admin", dependencies=[Depends(require_permission("admin:*"))])

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

_OUTPUT_DIR = Path(__file__).resolve().parents[3] / "data" / "crawlers" / "output"


# ============================================================
# 用户管理
# ============================================================

@router.get("/users")
async def list_users(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """用户列表（分页）。"""
    total = await db.scalar(select(func.count()).select_from(User))
    rows = await db.scalars(
        select(User).order_by(User.created_at.desc()).offset((page - 1) * size).limit(size)
    )
    items = [
        {
            "id": u.id,
            "username": u.username,
            "role": u.role,
            "is_active": u.is_active,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "updated_at": u.updated_at.isoformat() if u.updated_at else None,
        }
        for u in rows
    ]
    return ok(data={"items": items, "total": total or 0, "page": page, "size": size})


@router.post("/users", status_code=201)
async def create_user(req: dict, db: AsyncSession = Depends(get_db)):
    """创建用户（管理员代建）。"""
    username = (req.get("username") or "").strip()
    password = req.get("password") or ""
    role = req.get("role") or "user"
    if len(username) < 3 or len(password) < 6:
        return error(400, "用户名至少 3 字符，密码至少 6 字符")
    if role not in ("admin", "user", "guest"):
        return error(400, "角色非法")
    existing = await db.scalar(select(User).where(User.username == username))
    if existing is not None:
        return error(409, "用户名已存在")
    user = User(username=username, password_hash=hash_password(password), role=role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return ok(data={"id": user.id, "username": user.username, "role": user.role, "is_active": user.is_active})


@router.put("/users/{user_id}")
async def update_user(user_id: str, req: dict, db: AsyncSession = Depends(get_db)):
    """更新用户角色 / 启用状态。"""
    user = await db.get(User, user_id)
    if user is None:
        return error(404, "用户不存在")
    if "role" in req and req["role"] in ("admin", "user", "guest"):
        user.role = req["role"]
    if "status" in req:
        user.is_active = req["status"] == "active"
    await db.commit()
    return ok(data={"id": user.id, "username": user.username, "role": user.role, "is_active": user.is_active})


@router.delete("/users/{user_id}", status_code=204)
async def disable_user(user_id: str, db: AsyncSession = Depends(get_db)):
    """禁用用户（软删除，is_active=False）。"""
    user = await db.get(User, user_id)
    if user is None:
        return error(404, "用户不存在")
    user.is_active = False
    await db.commit()
    return None


# ============================================================
# 审计日志
# ============================================================

@router.get("/audit/logs")
async def audit_logs(
    category: str | None = Query(default=None, pattern="^(AUTH|GRAPH|DATA|ADMIN)$"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """审计日志查询（分页 + 类别过滤）。"""
    stmt = select(AuditLog)
    count_stmt = select(func.count()).select_from(AuditLog)
    if category:
        # action 以模块前缀命名（如 auth.login / admin.user.update），按前缀过滤
        prefix = category.lower() + "%"
        stmt = stmt.where(AuditLog.action.like(prefix))
        count_stmt = count_stmt.where(AuditLog.action.like(prefix))
    total = await db.scalar(count_stmt)
    rows = await db.scalars(
        stmt.order_by(AuditLog.created_at.desc()).offset((page - 1) * size).limit(size)
    )
    items = [
        {
            "id": log.id,
            "user_id": log.user_id,
            "action": log.action,
            "resource": log.resource,
            "resource_id": log.resource_id,
            "detail": log.detail,
            "ip_address": log.ip_address,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in rows
    ]
    return ok(data={"items": items, "total": total or 0, "page": page, "size": size})


# ============================================================
# 爬虫状态
# ============================================================

@router.get("/crawl/status")
async def crawl_status(db: AsyncSession = Depends(get_db)):
    """爬取状态监控：raw 表计数 + output JSONL 文件统计。"""
    raw_counts = {
        "jd": await db.scalar(select(func.count()).select_from(JDRaw)) or 0,
        "course": await db.scalar(select(func.count()).select_from(CourseRaw)) or 0,
        "paper": await db.scalar(select(func.count()).select_from(PaperRaw)) or 0,
        "community": await db.scalar(select(func.count()).select_from(CommunityRaw)) or 0,
    }

    # 从 output/{platform}.jsonl 统计各平台采集文件
    platforms = []
    output_total = 0
    if _OUTPUT_DIR.exists():
        for f in sorted(_OUTPUT_DIR.glob("*.jsonl")):
            platform = f.stem
            if platform not in PLATFORM_META:
                continue
            try:
                count = sum(1 for _ in f.open(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                count = 0
            output_total += count
            platforms.append({
                "platform": platform,
                "count": count,
                "file": f.name,
                "mtime": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone(timedelta(hours=8))).isoformat(),
            })

    # 按平台聚合（最新文件时间 + 累计条数）
    by_platform: dict[str, dict] = {}
    for p in platforms:
        meta = PLATFORM_META[p["platform"]]
        entry = by_platform.setdefault(p["platform"], {
            "id": p["platform"],
            "name": meta["name"],
            "level": meta["level"],
            "files": 0,
            "total_count": 0,
            "last_run": None,
        })
        entry["files"] += 1
        entry["total_count"] += p["count"]
        if entry["last_run"] is None or p["mtime"] > entry["last_run"]:
            entry["last_run"] = p["mtime"]

    return ok(data={
        "metrics": {
            "today_count": 0,  # 真实调度未运行，今日增量由 ETL 调度写入后统计
            "output_total": output_total,
            "raw": raw_counts,
        },
        "platforms": sorted(by_platform.values(), key=lambda x: (x["level"], x["id"])),
    })


# ============================================================
# 爬取管理（BE-M4-05）：手动触发爬取任务
# ============================================================

# 平台 ID（前端 PLATFORM_META 口径）→ Scrapy spider 名（crawl_platform 消费）
_PLATFORM_TO_SPIDER = {
    **{p: p for p in PLATFORM_META},
    "linkedin": "linkedin_public",
}


async def _enqueue_crawl(spider: str, keywords: list[str], task_id: str | None = None) -> None:
    """入队 ARQ crawl_platform 任务；队列不可用抛异常由调用方标记 failed。"""
    from arq import create_pool
    from arq.connections import RedisSettings
    from urllib.parse import urlparse

    parsed = urlparse(settings.arq_redis_url)
    redis_settings = RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int(parsed.path.lstrip("/") or "1"),
        password=parsed.password,
    )
    pool = await create_pool(redis_settings)
    try:
        # task_id 供 crawl_platform 实时写日志队列 + 更新任务状态（SSE 端点消费）
        await pool.enqueue_job("crawl_platform", spider_name=spider, keywords=keywords, task_id=task_id)
    finally:
        await pool.close()


@router.post("/crawl/trigger", status_code=202)
async def crawl_trigger(req: dict, db: AsyncSession = Depends(get_db)):
    """触发爬取任务（BE-M4-05，契约 /admin/crawl/trigger）。

    校验平台（PLATFORM_META 白名单）→ 建 TaskStatus(pending) → 入队 ARQ
    crawl_platform → 返回 task_id。队列不可用时标记任务 failed 并返回 500。
    """
    platform = (req.get("platform") or "").strip()
    keyword = (req.get("keyword") or "").strip()
    if platform not in _PLATFORM_TO_SPIDER:
        return error(400, f"未知平台: {platform}（可选: {', '.join(sorted(PLATFORM_META))}）")
    if not keyword:
        return error(400, "keyword 不能为空")

    task = TaskStatus(
        task_type="crawl",
        status="pending",
        result={"platform": platform, "keyword": keyword},
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    try:
        await _enqueue_crawl(_PLATFORM_TO_SPIDER[platform], [keyword], task_id=str(task.id))
    except Exception as e:
        task.status = "failed"
        task.error = f"任务入队失败: {e}"
        await db.commit()
        return error(500, f"爬取任务入队失败: {e}")

    return ok(data={"task_id": task.id, "platform": platform, "status": "pending"})


# ============================================================
# 爬虫实时日志（SSE）：手动触发后逐行推送 scrapy 终端输出
# ============================================================

def _crawl_task_payload(task) -> dict:
    """crawl 任务状态 → SSE data 载荷（TaskStatus ORM 对象不可直接 JSON 序列化）。"""
    return {
        "task_id": task.id,
        "task_type": task.task_type,
        "status": task.status,
        "progress": task.progress,
        "result": task.result,
        "error": task.error,
    }


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
    deadline = time.monotonic() + timeout
    while True:
        try:
            lines = await get_logs(task_uuid, offset)
        except Exception:
            lines = []
        for ln in lines:
            yield f"event: log\ndata: {json.dumps({'line': ln}, ensure_ascii=False)}\n\n"
        offset += len(lines)

        task = await get_task(task_uuid)
        if task is None:
            yield f"event: error\ndata: {json.dumps({'message': '任务不存在'}, ensure_ascii=False)}\n\n"
            return
        if task["status"] == "success":
            yield f"event: done\ndata: {json.dumps(task, ensure_ascii=False)}\n\n"
            return
        if task["status"] == "failed":
            yield f"event: error\ndata: {json.dumps(task, ensure_ascii=False)}\n\n"
            return
        yield f"event: progress\ndata: {json.dumps({'status': task['status'], 'progress': task['progress']}, ensure_ascii=False)}\n\n"
        if time.monotonic() >= deadline:
            yield f"event: error\ndata: {json.dumps({'message': '推送超时'}, ensure_ascii=False)}\n\n"
            return
        await asyncio.sleep(poll_interval)


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
        return error(400, "task_id 格式非法")

    from arq import create_pool
    from arq.connections import RedisSettings
    from urllib.parse import urlparse

    parsed = urlparse(settings.arq_redis_url)
    redis_settings = RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int(parsed.path.lstrip("/") or "1"),
        password=parsed.password,
    )

    async def _get_task(tid: str) -> dict | None:
        from app.core.database import async_session_factory
        from app.models.business import TaskStatus

        async with async_session_factory() as session:
            task = await session.get(TaskStatus, tid)
        return _crawl_task_payload(task) if task is not None else None

    async def _get_logs(tid: str, start: int) -> list[str]:
        pool = await create_pool(redis_settings)
        try:
            raw = await pool.lrange(f"crawl:log:{tid}", start, -1)
            return [ln.decode("utf-8", errors="replace") if isinstance(ln, bytes) else str(ln) for ln in raw]
        finally:
            await pool.close()

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
    total = await db.scalar(count_stmt) or 0
    rows = await db.scalars(
        stmt.order_by(TaskStatus.created_at.desc()).offset((page - 1) * size).limit(size)
    )
    return ok(data={"items": [_history_row(t) for t in rows], "total": total, "page": page, "size": size})


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
        "created_at": task.created_at.isoformat() if task.created_at else None,
    }


# ============================================================
# 岗位审核（AL-M4-01 新岗位发现：候选池 pending 列表 + 审核流转）
# ============================================================

@router.get("/positions/pending")
async def positions_pending(
    state: str | None = Query(default=None, pattern="^(candidate|emerging|stable|declining)$"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """待审核岗位列表（新岗位发现候选池）。

    默认返回 candidate（待 admin 审核是否晋升 emerging），可切换状态过滤。
    """
    from app.models.business import DiscoveryCandidate

    stmt = select(DiscoveryCandidate)
    count_stmt = select(func.count()).select_from(DiscoveryCandidate)
    if state:
        stmt = stmt.where(DiscoveryCandidate.state == state)
        count_stmt = count_stmt.where(DiscoveryCandidate.state == state)
    total = await db.scalar(count_stmt)
    rows = await db.scalars(
        stmt.order_by(DiscoveryCandidate.detected_at.desc())
        .offset((page - 1) * size)
        .limit(size)
    )
    items = [
        {
            "id": c.id,
            "position_name": c.position_name,
            "state": c.state,
            "features": c.features,
            "confidence": c.confidence,
            "evidence_refs": c.evidence_refs,
            "seed_matched": c.seed_matched,
            "rag_matched": c.rag_matched,
            "definition_draft": c.definition_draft,
            "detected_at": c.detected_at,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        }
        for c in rows
    ]
    return ok(data={"items": items, "total": total or 0, "page": page, "size": size})


@router.post("/positions/{candidate_id}/review")
async def review_position(
    candidate_id: str,
    req: dict,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("admin:*")),
):
    """审核 candidate：approve → emerging / reject → rejected。

    流程：读候选池 → 组装 CandidatePosition → 状态机校验（emerging 需
    置信度 ≥ 0.6 AND 源 ≥ 2）→ Neo4j Position.status 同步 → 写审计日志
    → 更新候选池状态。

    Args:
        req: {"action": "approve" | "reject", "reason": "..."}，reason 必填
    """
    from app.core.database import neo4j_driver
    from app.models.business import DiscoveryCandidate
    from app.services.discovery.schemas import CandidatePosition, DiscoveryFeatures, PositionState
    from app.services.discovery.state_machine import PositionStateMachine

    action = req.get("action")
    reason = (req.get("reason") or "").strip()
    if action not in ("approve", "reject"):
        return error(400, "action 必须为 approve 或 reject")
    if not reason:
        return error(400, "审核必须填写 reason")

    cand_row = await db.get(DiscoveryCandidate, candidate_id)
    if cand_row is None:
        return error(404, "候选岗位不存在")
    if cand_row.state != "candidate":
        return error(409, f"候选岗位当前状态 {cand_row.state}，不可审核")

    features = DiscoveryFeatures(**cand_row.features)
    candidate = CandidatePosition(
        candidate_id=cand_row.id,
        position_name=cand_row.position_name,
        state=PositionState.CANDIDATE,
        features=features,
        detected_at=cand_row.detected_at,
        evidence_refs=cand_row.evidence_refs,
        seed_matched=cand_row.seed_matched,
        rag_matched=cand_row.rag_matched,
        definition_draft=cand_row.definition_draft,
    )
    target = PositionState.EMERGING if action == "approve" else PositionState.REJECTED
    if action == "approve":
        # 置信度 ≥ 0.6 AND 源多样性 ≥ 2 才允许晋升（设计文档 7.2.4 阈值表）
        from app.services.discovery.state_machine import can_promote_to_emerging

        conf = cand_row.confidence or {}
        if not can_promote_to_emerging(candidate, confidence=float(conf.get("final_confidence", 0.0))):
            return error(400, "置信度 < 0.6 或独立源 < 2，不满足 emerging 晋升条件")

    machine = PositionStateMachine()
    with neo4j_driver.session() as neo4j_session:
        updated = machine.persist(
            neo4j_session,
            candidate,
            target,
            db=db,
            operator=current_user.get("sub") or current_user.get("user_id", "admin"),
            reason=reason,
        )

    cand_row.state = updated.state.value
    await db.commit()

    return ok(
        data={
            "id": cand_row.id,
            "position_name": cand_row.position_name,
            "state": cand_row.state,
            "reason": reason,
        },
        msg=f"已{'通过晋升 emerging' if action == 'approve' else '驳回'}: {cand_row.position_name}",
    )


# ============================================================
# LLM provider 配置（持久化到 llm_providers.yaml）
# ============================================================

_LLM_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "llm_providers.yaml"


def mask_secret(value: str) -> str:
    """密钥打码：保留后 4 位，其余掩码；空值返回空串。"""
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


def mask_providers(providers: list[dict]) -> list[dict]:
    """对 provider 列表的 api_key 打码（不修改入参）。"""
    return [{**p, "api_key": mask_secret(str(p.get("api_key") or ""))} for p in providers]


def validate_providers(providers: list) -> str | None:
    """校验 provider 列表，返回错误信息或 None。

    约束：非空列表；name 唯一且为安全字符；base_url 为 http(s) 地址；
    model 非空；priority 正整数且唯一；enabled 布尔。
    """
    if not isinstance(providers, list) or not providers:
        return "providers 必须是非空列表"
    seen_names: set[str] = set()
    seen_priorities: set[int] = set()
    for i, p in enumerate(providers):
        if not isinstance(p, dict):
            return f"第 {i + 1} 个 provider 必须是对象"
        name = (p.get("name") or "").strip()
        base_url = (p.get("base_url") or "").strip()
        model = (p.get("model") or "").strip()
        if not name:
            return f"第 {i + 1} 个 provider 缺少 name"
        if not re.match(r"^[A-Za-z0-9_-]+$", name):
            return f"name '{name}' 只能包含字母/数字/下划线/短横线"
        if name in seen_names:
            return f"name '{name}' 重复"
        seen_names.add(name)
        if not base_url.startswith(("http://", "https://")):
            return f"provider '{name}' 的 base_url 必须以 http(s):// 开头"
        if not model:
            return f"provider '{name}' 缺少 model"
        priority = p.get("priority")
        if not isinstance(priority, int) or priority < 1:
            return f"provider '{name}' 的 priority 必须为正整数"
        if priority in seen_priorities:
            return f"priority {priority} 重复（provider '{name}'）"
        seen_priorities.add(priority)
        if not isinstance(p.get("enabled", True), bool):
            return f"provider '{name}' 的 enabled 必须是布尔值"
    return None


def load_llm_config(path: Path) -> dict:
    """读取 yaml 配置。"""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_llm_config(path: Path, providers: list) -> dict:
    """校验并写回 yaml，返回写回后的完整配置。

    api_key 为空白或含掩码（*）时保持原值，明文才更新；
    写回保留原文件头部注释（到顶层键 providers 之前）。
    """
    err = validate_providers(providers)
    if err:
        raise ValueError(err)

    text = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    old = {
        p["name"]: p for p in data.get("providers", [])
        if isinstance(p, dict) and p.get("name")
    }

    clean = []
    for p in providers:
        name = (p.get("name") or "").strip()
        api_key = (p.get("api_key") or "").strip()
        if not api_key or "*" in api_key:
            api_key = (old.get(name) or {}).get("api_key", "")
        entry = {
            "name": name,
            "priority": int(p["priority"]),
            "base_url": (p.get("base_url") or "").strip(),
            "api_key": api_key,
            "model": (p.get("model") or "").strip(),
            "supports_function_calling": bool(p.get("supports_function_calling", True)),
            "enabled": bool(p.get("enabled", True)),
        }
        # provider 特定请求参数（如 deepseek 关闭思考模式 thinking.type=disabled），非 dict 忽略
        extra_body = p.get("extra_body")
        if isinstance(extra_body, dict) and extra_body:
            entry["extra_body"] = extra_body
        clean.append(entry)
    data["providers"] = clean

    # 保留原文件头部注释块（到顶层键 providers: 为止），rest 由 dump 生成
    parts = re.split(r"^providers:\s*$", text, maxsplit=1, flags=re.M)
    header = parts[0] if len(parts) == 2 else ""
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
    Path(path).write_text(header + body, encoding="utf-8")

    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


@router.get("/llm-config")
async def get_llm_config():
    """读取当前生效 LLM provider 配置（api_key 打码，不明文回显）。"""
    try:
        cfg = load_llm_config(_LLM_CONFIG_PATH)
    except (OSError, yaml.YAMLError):
        return error(500, "LLM 配置读取失败")
    cfg["providers"] = mask_providers(cfg.get("providers", []))
    return ok(data=cfg)


@router.put("/llm-config")
async def update_llm_config(req: dict):
    """保存 LLM provider 配置（持久化到 yaml，api_key 留空/掩码保持原值）。"""
    providers = req.get("providers")
    try:
        saved = save_llm_config(_LLM_CONFIG_PATH, providers)
    except ValueError as e:
        return error(400, str(e))
    except (OSError, yaml.YAMLError):
        return error(500, "LLM 配置保存失败")
    saved["providers"] = mask_providers(saved.get("providers", []))
    return ok(data=saved)
