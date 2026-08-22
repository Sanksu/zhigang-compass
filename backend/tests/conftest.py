"""共享测试夹具（08-14 审查：RSA 密钥对生成在 4 个测试文件重复实现，收敛于此）。

- tmp_rsa_keys：临时 RSA 密钥对（CI 无 keys/*.pem，gitignore 排除）
- _use_tmp_keys：全局注入临时密钥路径（create/decode_token 均经 settings 读取）
"""

import asyncio
import inspect
import sys
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.config import settings


@pytest.fixture(scope="session")
def tmp_rsa_keys(tmp_path_factory):
    """生成临时 RSA 密钥对并返回 (私钥路径, 公钥路径)。"""
    tmp = tmp_path_factory.mktemp("jwt-keys")
    priv_path = tmp / "private.pem"
    pub_path = tmp / "public.pem"

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    pub_path.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return str(priv_path), str(pub_path)


@pytest.fixture(autouse=True)
def _use_tmp_keys(tmp_rsa_keys):
    """全局注入临时密钥路径（create/decode 均经 settings 读取）。"""
    priv, pub = tmp_rsa_keys
    with patch.object(settings, "jwt_private_key_path", priv), \
         patch.object(settings, "jwt_public_key_path", pub):
        yield


async def _close_database_resources(database: Any) -> list[tuple[str, Exception]]:
    """按应用生命周期语义关闭数据库模块中的已创建资源。"""
    closers: tuple[tuple[str, Callable[[], Awaitable[None] | None]], ...] = (
        ("async_neo4j_driver", database.async_neo4j_driver.close),
        ("neo4j_driver", database.neo4j_driver.close),
        ("redis_client", database.redis_client.aclose),
        ("engine", database.engine.dispose),
    )
    errors: list[tuple[str, Exception]] = []

    for resource_name, close in closers:
        try:
            result = close()
            if inspect.isawaitable(result):
                await result
        except Exception as error:
            errors.append((resource_name, error))

    return errors


def _report_cleanup_failures(session: pytest.Session, errors: list[tuple[str, Exception]]) -> None:
    """将所有清理错误输出至终端，并保留原有的失败退出状态。"""
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        for resource_name, error in errors:
            reporter.write_line(f"ERROR: failed to close {resource_name}: {error!r}")

    if session.exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_sessionfinish(session: pytest.Session, exitstatus: int | pytest.ExitCode) -> None:
    """关闭已加载数据库模块的资源，并将清理失败计入测试会话结果。"""
    database = sys.modules.get("app.core.database")
    if database is None:
        return

    errors = asyncio.run(_close_database_resources(database))
    if errors:
        _report_cleanup_failures(session, errors)
