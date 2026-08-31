"""审计日志端点测试（audit，第八轮审查 P1-14 补测）。

审计查询属安全红线端点：覆盖默认分页与字段映射 / 类别过滤（action 前缀
LIKE 同时作用于行查询与计数）/ 空结果 / 非法 category 传输层 422 /
非 admin 403（RBAC 由 admin facade 根 router 挂载 require_permission）。
项目纯函数风格：asyncio.run 直调端点协程，注入 fake async session。
"""

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.deps import require_permission
from app.api.v1.admin_routes import audit as mod


def _log(action="admin.user.update", resource="user", resource_id="abc") -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        user_id=str(uuid.uuid4()),
        action=action,
        resource=resource,
        resource_id=resource_id,
        detail={"role": "admin"},
        ip_address="127.0.0.1",
        created_at=datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc),
    )


class _FakeSession:
    """audit 端点用到的 session 面：scalar（计数）/ scalars（行查询），均记录语句。"""

    def __init__(self, *, scalar=0, scalars_rows=None):
        self._scalar = scalar
        self._scalars_rows = scalars_rows if scalars_rows is not None else []
        self.scalar_stmts = []
        self.scan_stmts = []

    async def scalar(self, stmt):
        self.scalar_stmts.append(stmt)
        return self._scalar

    async def scalars(self, stmt):
        self.scan_stmts.append(stmt)
        return self._scalars_rows


class TestAuditLogs:
    def test_默认分页与字段映射(self):
        log = _log()
        db = _FakeSession(scalar=1, scalars_rows=[log])
        resp = asyncio.run(mod.audit_logs(category=None, page=1, size=20, db=db))
        data = resp.data
        assert set(data) == {"items", "total", "page", "size"}
        assert data["total"] == 1 and data["page"] == 1 and data["size"] == 20
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["id"] == 7
        assert item["action"] == "admin.user.update"
        assert item["resource"] == "user" and item["resource_id"] == "abc"
        assert item["detail"] == {"role": "admin"}
        assert item["ip_address"] == "127.0.0.1"
        assert uuid.UUID(item["user_id"])  # user_id 为 UUID 字符串
        assert item["created_at"].startswith("2026-08-29T12:00")

    def test_类别过滤按action前缀且计数同步过滤(self):
        log = _log(action="admin.user.update")
        db = _FakeSession(scalar=1, scalars_rows=[log])
        resp = asyncio.run(mod.audit_logs(category="ADMIN", page=1, size=20, db=db))
        assert resp.data["total"] == 1
        # 行查询与计数语句须同时带 action LIKE 前缀过滤（category 大写 → 前缀小写）；
        # 行查询语句另带 offset/limit 绑定参数，故按值包含断言
        for stmt in (db.scan_stmts[0], db.scalar_stmts[0]):
            compiled = stmt.compile()
            assert "audit_logs.action LIKE" in str(compiled)
            assert "admin%" in list(compiled.params.values())
        # 未过滤时无 WHERE 子句
        db2 = _FakeSession(scalar=0)
        asyncio.run(mod.audit_logs(category=None, page=1, size=20, db=db2))
        assert "WHERE" not in str(db2.scan_stmts[0].compile())

    def test_空结果正常返回空态结构(self):
        db = _FakeSession(scalar=0, scalars_rows=[])
        resp = asyncio.run(mod.audit_logs(category=None, page=1, size=20, db=db))
        assert resp.data == {"total": 0, "page": 1, "size": 20, "items": []}


class TestCategoryValidation:
    """category 枚举白名单在传输层（Query pattern）拦截：非法值 422 不触库，合法值放行。"""

    @pytest.fixture()
    def make_client(self):
        def _make(db: _FakeSession) -> TestClient:
            from app.core.database import get_db

            async def _fake_db():
                return db

            app = FastAPI()
            app.include_router(mod.router, prefix="/admin")
            app.dependency_overrides[get_db] = _fake_db
            return TestClient(app)

        return _make

    def test_非法category传输层422(self, make_client):
        client = make_client(_FakeSession())
        resp = client.get("/admin/audit/logs", params={"category": "BOGUS"})
        assert resp.status_code == 422

    def test_合法category枚举通过校验(self, make_client):
        # AUTH/GRAPH/DATA/ADMIN 过白名单后端点正常执行（db 以 fake 注入）
        client = make_client(_FakeSession(scalar=1, scalars_rows=[_log(action="auth.login")]))
        resp = client.get("/admin/audit/logs", params={"category": "AUTH"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0 and body["data"]["total"] == 1
        assert body["data"]["items"][0]["action"] == "auth.login"


def _run_guard(role: str):
    """直调 require_permission("admin:*") 依赖（audit 端点经 admin facade 根 router 挂载）。"""

    async def _inner():
        check = require_permission("admin:*")
        return await check({"sub": str(uuid.uuid4()), "role": role})

    return asyncio.run(_inner())


class TestAdminRbacGuard:
    """非 admin 角色访问审计端点 403（守卫依赖与 accounts 共用）。"""

    def test_admin角色放行(self):
        assert _run_guard("admin")["role"] == "admin"

    def test_user与guest角色403(self):
        for role in ("user", "guest"):
            with pytest.raises(HTTPException) as exc:
                _run_guard(role)
            assert exc.value.status_code == 403
