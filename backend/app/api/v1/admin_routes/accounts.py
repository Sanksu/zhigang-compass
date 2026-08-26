"""管理后台用户管理路由（RBAC admin only）。

对齐契约 /api/v1/admin/users*：列表/创建/更新/物理删除（GDPR/PIPL 删除权）。
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import iso, paged_ok, paginate, resolve_operator
from app.api.deps import require_permission
from app.core.database import get_db
from app.core.errors import ERR_CONFLICT, ERR_NOT_FOUND, ERR_VALIDATION
from app.core.security import hash_password
from app.models.business import AuditLog, ResumeFile, User
from app.schemas.admin_requests import CreateUserRequest, UpdateUserRequest
from app.schemas.common import error, ok

router = APIRouter()


@router.get("/users")
async def list_users(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("admin:*")),
):
    """用户列表（分页）。"""
    stmt = select(User).order_by(User.created_at.desc())
    rows, total = await paginate(
        db, stmt, page, size, count_stmt=select(func.count()).select_from(User)
    )
    # M5 修复：管理域四端点全量审计（合规留痕，低频管理动作可接受）
    db.add(AuditLog(
        user_id=current_user.get("sub", ""),
        action="admin.user.list",
        resource="user",
        resource_id="*",
        detail={"page": page, "size": size},
    ))
    await db.commit()
    items = [
        {
            "id": u.id,
            "username": u.username,
            "role": u.role,
            "is_active": u.is_active,
            "created_at": iso(u.created_at),
            "updated_at": iso(u.updated_at),
        }
        for u in rows
    ]
    return paged_ok(items, total, page, size)


@router.post("/users", status_code=201)
async def create_user(
    req: CreateUserRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("admin:*")),
):
    """创建用户（管理员代建）。username/password 长度与 role 枚举由 Pydantic 校验。"""
    operator, operator_err = resolve_operator(current_user)
    if operator_err is not None:
        return operator_err
    username = req.username.strip()
    password = req.password
    role = req.role
    existing = await db.scalar(select(User).where(User.username == username))
    if existing is not None:
        return error(ERR_CONFLICT, "用户名已存在")
    user = User(username=username, password_hash=hash_password(password), role=role)
    db.add(user)
    # M5 修复：用户创建属敏感管理操作，补审计（含目标用户与被授予角色）
    db.add(AuditLog(
        user_id=operator,
        action="admin.user.create",
        resource="user",
        resource_id=username,
        detail={"role": role},
    ))
    await db.commit()
    await db.refresh(user)
    return ok(data={"id": user.id, "username": user.username, "role": user.role, "is_active": user.is_active})


@router.put("/users/{user_id}")
async def update_user(
    user_id: str,
    req: UpdateUserRequest,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("admin:*")),
):
    """更新用户角色 / 启用状态（M6 自保护：不可降级/禁用自己；部分更新）。"""
    operator, operator_err = resolve_operator(current_user)
    if operator_err is not None:
        return operator_err
    user = await db.get(User, user_id)
    if user is None:
        return error(ERR_NOT_FOUND, "用户不存在", http_status=404)
    fields_set = req.model_fields_set
    # 自保护：当前登录管理员不允许降级自己的角色或禁用自己，避免后台锁死
    if user_id == current_user.get("sub"):
        if "status" in fields_set and req.status != "active":
            return error(ERR_VALIDATION, "不能禁用当前登录账户")
        if "role" in fields_set and req.role != "admin":
            return error(ERR_VALIDATION, "不能降级当前登录账户")
    if "role" in fields_set and req.role is not None:
        user.role = req.role
    if "status" in fields_set and req.status is not None:
        user.is_active = req.status == "active"
    # M5 修复：改角色/禁用属提权与锁账号敏感操作，补审计（记录本次变更明细）
    db.add(AuditLog(
        user_id=operator,
        action="admin.user.update",
        resource="user",
        resource_id=user_id,
        detail={"role": req.role, "status": req.status},
    ))
    await db.commit()
    return ok(data={"id": user.id, "username": user.username, "role": user.role, "is_active": user.is_active})


@router.delete("/users/{user_id}", status_code=204)
async def disable_user(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("admin:*")),
):
    """物理删除用户（GDPR/PIPL 删除权：删除用户时一并清理其简历文件归属）。

    与「禁用」（PUT /users/{id} 置 is_active=False）语义区分：本端点彻底移除
    users 行，并删除该用户的 resume_files 归属（含简历原文字节，§8.1 上传者
    本人数据）；resume_cache 按内容哈希全局共享，其他用户引用时保留不删。
    audit_logs 无外键约束（索引仅加速查询），物理删除不阻塞历史审计保留。
    返回 204 无 body，错误以 HTTPException 携带 400/404 语义。
    """
    if user_id == current_user.get("sub"):
        raise HTTPException(status_code=400, detail="不能删除当前登录账户")
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    # 清理该用户的简历原文归属（含文件字节）；resume_cache 共享缓存不连坐
    await db.execute(sa_delete(ResumeFile).where(ResumeFile.user_id == user_id))
    # M5 修复：GDPR/PIPL 物理删除是最敏感操作，补审计（删除前记录目标用户信息）
    db.add(AuditLog(
        user_id=current_user.get("sub", ""),
        action="admin.user.delete",
        resource="user",
        resource_id=user_id,
        detail={"username": user.username, "role": user.role},
    ))
    await db.delete(user)
    await db.commit()
    return None
