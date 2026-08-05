"""require_role 依赖测试（resume/match 端点 user+ 认证语义）。

设计文档 §2.4.3/2.4.4 "user+"：guest 角色即使已登录也无权调用简历/匹配端点。
"""

import asyncio

import pytest
from fastapi import HTTPException

from app.api.deps import require_role


def _run(min_role: str, role: str):
    async def _inner():
        check = require_role(min_role)
        return await check({"role": role})

    return asyncio.run(_inner())


def test_require_role_user_allows_user_and_admin():
    assert _run("user", "user") == {"role": "user"}
    assert _run("user", "admin") == {"role": "admin"}


def test_require_role_user_rejects_guest():
    with pytest.raises(HTTPException) as exc:
        _run("user", "guest")
    assert exc.value.status_code == 403


def test_require_role_admin_rejects_user_and_guest():
    with pytest.raises(HTTPException):
        _run("admin", "user")
    with pytest.raises(HTTPException):
        _run("admin", "guest")


def test_require_role_unknown_role_rejected():
    # 未注册角色按 guest 处理（拒绝访问）
    with pytest.raises(HTTPException):
        _run("user", "superuser")
