"""账号管理端点测试（accounts，第八轮审查 P1-14 补测）。

用户增删改/禁用属安全红线端点：覆盖创建（重名冲突 / 角色枚举校验）/
更新（部分更新 / 非法 UUID 4000 / 自保护）/ 物理删除（resume_files 归属
连带清理 / 非法 UUID 400 不再 500）/ 列表映射，以及 resolve_operator
守卫 + AuditLog 操作者断言（#670 修复点：operator 经 sub 解析）。
项目纯函数风格：asyncio.run 直调端点协程，注入 fake async session。
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import Delete

from app.api.deps import require_permission
from app.api.v1.admin_routes import accounts as mod
from app.core.security import verify_password
from app.models.business import AuditLog, User


def _resp_code(resp):
    """error() 返回 JSONResponse：取业务码。"""
    return json.loads(resp.body)["code"]


def _user_row(uid=None, username="target", role="user", is_active=True) -> SimpleNamespace:
    return SimpleNamespace(
        id=uid or str(uuid.uuid4()),
        username=username,
        role=role,
        is_active=is_active,
        password_hash="old-hash",
        created_at=datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc),
    )


class _FakeSession:
    """accounts 端点用到的 session 面：scalar/scalars/get/execute/add/delete/commit/refresh。"""

    def __init__(self, *, scalar=None, scalars_rows=None, get=None):
        self._scalar = scalar
        self._scalars_rows = scalars_rows if scalars_rows is not None else []
        self._get = get
        self.added = []
        self.deleted = []
        self.executed = []
        self.scalar_stmts = []
        self.scan_stmts = []
        self.committed = False

    async def scalar(self, stmt):
        self.scalar_stmts.append(stmt)
        return self._scalar

    async def scalars(self, stmt):
        self.scan_stmts.append(stmt)
        return self._scalars_rows

    async def get(self, model, pk):
        return self._get

    async def execute(self, stmt):
        self.executed.append(stmt)
        return None

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def refresh(self, obj):
        pass

    async def commit(self):
        """模拟 flush+commit：应用 User 的 Python 端列默认值（真实 DB 在 flush 时生效）。

        id default=lambda: str(uuid4())、is_active default=True（role 由端点显式传入）。
        """
        self.committed = True
        for obj in self.added:
            if isinstance(obj, User):
                if obj.id is None:
                    obj.id = str(uuid.uuid4())
                if obj.is_active is None:
                    obj.is_active = True


_ADMIN = {"sub": str(uuid.uuid4()), "role": "admin"}


def _single_audit(db) -> AuditLog:
    """取本次操作写入的唯一审计记录（create 场景 added 里另有 User 行）。"""
    entries = [a for a in db.added if isinstance(a, AuditLog)]
    assert len(entries) == 1
    return entries[0]


class TestListUsers:
    def test_列表映射与分页字段(self):
        rows = [_user_row(username="alice", role="admin"), _user_row(username="bob")]
        db = _FakeSession(scalar=2, scalars_rows=rows)
        resp = asyncio.run(mod.list_users(page=1, size=20, db=db, current_user=_ADMIN))
        data = resp.data
        assert data["total"] == 2 and data["page"] == 1 and data["size"] == 20
        item = data["items"][0]
        assert item["username"] == "alice" and item["role"] == "admin" and item["is_active"] is True
        assert item["created_at"].startswith("2026-08-29T12:00")
        # 管理域全量审计：操作者须为 sub 解析出的 UUID（#670 修复点）
        audit = _single_audit(db)
        assert audit.action == "admin.user.list"
        assert audit.user_id == _ADMIN["sub"]
        assert audit.resource == "user" and audit.resource_id == "*"
        assert audit.detail == {"page": 1, "size": 20}
        assert db.committed


class TestCreateUser:
    def test_创建成功密码哈希落库并写审计(self):
        db = _FakeSession(scalar=None)  # 重名查询无既有行
        req = mod.CreateUserRequest(username="newuser", password="secret123", role="user")
        resp = asyncio.run(mod.create_user(req=req, db=db, current_user=_ADMIN))
        # 响应体：新用户字段完整，id 为合法 UUID（fake commit 应用 Python 端默认值）
        assert resp.code == 0
        assert resp.data["username"] == "newuser"
        assert resp.data["role"] == "user" and resp.data["is_active"] is True
        assert uuid.UUID(resp.data["id"])
        # 密码必须哈希落库（明文不得入库）
        user = next(a for a in db.added if isinstance(a, User))
        assert user.password_hash != "secret123"
        assert verify_password("secret123", user.password_hash)
        # 审计：action / 目标用户 / 授予角色 / 操作者经 sub 解析（#670）
        audit = _single_audit(db)
        assert audit.action == "admin.user.create"
        assert audit.user_id == _ADMIN["sub"]
        assert audit.resource == "user" and audit.resource_id == "newuser"
        assert audit.detail == {"role": "user"}
        assert db.committed

    def test_用户名重复返回冲突且不落库(self):
        db = _FakeSession(scalar=_user_row(username="newuser"))
        req = mod.CreateUserRequest(username="newuser", password="secret123")
        resp = asyncio.run(mod.create_user(req=req, db=db, current_user=_ADMIN))
        assert _resp_code(resp) == 4090  # ERR_CONFLICT
        assert not db.added and not db.committed

    def test_操作者非UUID返回校验错误不落库(self):
        db = _FakeSession(scalar=None)
        req = mod.CreateUserRequest(username="newuser", password="secret123")
        resp = asyncio.run(mod.create_user(
            req=req, db=db, current_user={"sub": "not-a-uuid", "role": "admin"},
        ))
        assert _resp_code(resp) == 4000  # resolve_operator 守卫
        assert not db.added and not db.committed


class TestUpdateUser:
    def test_仅更新角色保留启用状态(self):
        row = _user_row(role="user", is_active=True)
        db = _FakeSession(get=row)
        req = mod.UpdateUserRequest(role="admin")
        resp = asyncio.run(mod.update_user(
            user_id=row.id, req=req, db=db, current_user=_ADMIN,
        ))
        assert row.role == "admin" and row.is_active is True
        assert resp.data["role"] == "admin" and resp.data["is_active"] is True
        # 审计记录本次变更明细（未传字段为 None）+ 操作者经 sub 解析（#670）
        audit = _single_audit(db)
        assert audit.action == "admin.user.update"
        assert audit.user_id == _ADMIN["sub"]
        assert audit.resource_id == row.id
        assert audit.detail == {"role": "admin", "status": None}
        assert db.committed

    def test_禁用用户状态落库(self):
        row = _user_row(is_active=True)
        db = _FakeSession(get=row)
        req = mod.UpdateUserRequest(status="disabled")
        resp = asyncio.run(mod.update_user(
            user_id=row.id, req=req, db=db, current_user=_ADMIN,
        ))
        assert row.is_active is False
        assert resp.data["is_active"] is False and resp.data["role"] == "user"
        assert _single_audit(db).detail == {"role": None, "status": "disabled"}
        assert db.committed

    def test_非法user_id返回校验错误(self):
        db = _FakeSession(get=_user_row())
        resp = asyncio.run(mod.update_user(
            user_id="not-a-uuid", req=mod.UpdateUserRequest(role="admin"),
            db=db, current_user=_ADMIN,
        ))
        # 项目口径：ERR_VALIDATION 业务码 4000（HTTP 422），非 500
        assert _resp_code(resp) == 4000
        assert not db.added and not db.committed

    def test_用户不存在返回404(self):
        db = _FakeSession(get=None)
        resp = asyncio.run(mod.update_user(
            user_id=str(uuid.uuid4()), req=mod.UpdateUserRequest(role="admin"),
            db=db, current_user=_ADMIN,
        ))
        assert _resp_code(resp) == 4040

    def test_不能禁用当前登录账户(self):
        row = _user_row(uid=_ADMIN["sub"], is_active=True)
        db = _FakeSession(get=row)
        resp = asyncio.run(mod.update_user(
            user_id=row.id, req=mod.UpdateUserRequest(status="disabled"),
            db=db, current_user=_ADMIN,
        ))
        assert _resp_code(resp) == 4000
        assert row.is_active is True
        assert not db.added and not db.committed

    def test_不能降级当前登录账户(self):
        row = _user_row(uid=_ADMIN["sub"], role="admin")
        db = _FakeSession(get=row)
        resp = asyncio.run(mod.update_user(
            user_id=row.id, req=mod.UpdateUserRequest(role="user"),
            db=db, current_user=_ADMIN,
        ))
        assert _resp_code(resp) == 4000
        assert row.role == "admin"
        assert not db.added and not db.committed

    def test_操作者非UUID返回校验错误不动目标用户(self):
        row = _user_row(role="user")
        db = _FakeSession(get=row)
        resp = asyncio.run(mod.update_user(
            user_id=row.id, req=mod.UpdateUserRequest(role="admin"),
            db=db, current_user={"sub": "admin", "role": "admin"},
        ))
        assert _resp_code(resp) == 4000
        assert row.role == "user"
        assert not db.added and not db.committed


class TestDeleteUser:
    def test_物理删除清理简历归属并写审计(self):
        row = _user_row(username="gone", role="user")
        db = _FakeSession(get=row)
        resp = asyncio.run(mod.disable_user(user_id=row.id, db=db, current_user=_ADMIN))
        assert resp is None  # 204 无 body
        # GDPR/PIPL：先删该用户 resume_files 归属（共享 resume_cache 不连坐）
        assert len(db.executed) == 1
        stmt = db.executed[0]
        assert isinstance(stmt, Delete) and stmt.table.name == "resume_files"
        assert db.deleted == [row] and db.committed
        # 审计：删除前记录目标用户信息 + 操作者经 sub 解析（#670）
        audit = _single_audit(db)
        assert audit.action == "admin.user.delete"
        assert audit.user_id == _ADMIN["sub"]
        assert audit.resource_id == row.id
        assert audit.detail == {"username": "gone", "role": "user"}

    def test_非法UUID返回400不再500(self):
        db = _FakeSession(get=_user_row())
        with pytest.raises(HTTPException) as exc:
            asyncio.run(mod.disable_user(
                user_id="not-a-uuid", db=db, current_user=_ADMIN,
            ))
        # 本端点沿用 HTTPException 口径：400（而非 db.get 撞 UUID 列解析 500）
        assert exc.value.status_code == 400
        assert exc.value.detail == "user_id 格式非法"
        assert not db.executed and not db.deleted and not db.committed

    def test_大写UUID规范化后命中目标(self):
        row = _user_row()
        db = _FakeSession(get=row)
        asyncio.run(mod.disable_user(user_id=row.id.upper(), db=db, current_user=_ADMIN))
        assert db.deleted == [row]
        assert _single_audit(db).resource_id == row.id  # parse_uuid 规范化为小写

    def test_不能删除当前登录账户(self):
        row = _user_row(uid=_ADMIN["sub"])
        db = _FakeSession(get=row)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(mod.disable_user(user_id=row.id, db=db, current_user=_ADMIN))
        assert exc.value.status_code == 400
        assert not db.deleted and not db.committed

    def test_用户不存在返回404(self):
        db = _FakeSession(get=None)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(mod.disable_user(
                user_id=str(uuid.uuid4()), db=db, current_user=_ADMIN,
            ))
        assert exc.value.status_code == 404

    def test_操作者非UUID返回校验错误不删除(self):
        row = _user_row()
        db = _FakeSession(get=row)
        resp = asyncio.run(mod.disable_user(
            user_id=row.id, db=db, current_user={"sub": "not-a-uuid", "role": "admin"},
        ))
        # 守卫错误返回 JSONResponse（显式 Response 覆盖 204），非 HTTPException
        assert _resp_code(resp) == 4000
        assert not db.executed and not db.deleted and not db.committed


class TestRequestModels:
    """请求体 Pydantic 防线：角色/状态枚举与长度校验（422 + code 4000 由全局处理器转）。"""

    def test_创建非法角色或长度不合规拒绝(self):
        with pytest.raises(ValidationError):
            mod.CreateUserRequest(username="newuser", password="secret123", role="superadmin")
        with pytest.raises(ValidationError):
            mod.CreateUserRequest(username="ab", password="secret123")  # username < 3
        with pytest.raises(ValidationError):
            mod.CreateUserRequest(username="newuser", password="123")  # password < 6

    def test_更新非法角色或状态拒绝(self):
        with pytest.raises(ValidationError):
            mod.UpdateUserRequest(role="root")
        with pytest.raises(ValidationError):
            mod.UpdateUserRequest(status="frozen")


def _run_guard(role: str):
    """直调 require_permission("admin:*") 依赖（同 tests/auth/test_require_role.py 模式）。"""

    async def _inner():
        check = require_permission("admin:*")
        return await check({"sub": str(uuid.uuid4()), "role": role})

    return asyncio.run(_inner())


class TestAdminRbacGuard:
    """accounts 四端点共用 require_permission("admin:*")：非 admin 一律 403。"""

    def test_admin角色放行并透传payload(self):
        payload = {"sub": str(uuid.uuid4()), "role": "admin"}

        async def _inner():
            return await require_permission("admin:*")(payload)

        assert asyncio.run(_inner()) == payload

    def test_user与guest角色403(self):
        for role in ("user", "guest"):
            with pytest.raises(HTTPException) as exc:
                _run_guard(role)
            assert exc.value.status_code == 403
