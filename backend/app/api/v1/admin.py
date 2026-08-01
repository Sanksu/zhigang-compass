"""管理后台路由：用户管理 / 审计日志 / 爬虫状态 / 岗位审核（RBAC admin only）。

对齐契约 /api/v1/admin/*。岗位审核（positions/pending）依赖 LLM 抽取信号，
当前返回空列表（M3/M4 交付后接入真实数据）。
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re

import yaml
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_permission
from app.core.database import get_db
from app.core.security import hash_password
from app.models.business import AuditLog, User
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
# 岗位审核（LLM 抽取信号未上线 → 空）
# ============================================================

@router.get("/positions/pending")
async def positions_pending():
    """待审核岗位列表。依赖 LLM 抽取 + 发现检测器（AL-M3/M4 交付），当前为空。"""
    return ok(data={"items": [], "total": 0})


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
