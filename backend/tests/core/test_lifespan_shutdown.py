"""P2 回归：lifespan 关闭路径释放双 Neo4j 驱动 / Redis / PostgreSQL 连接池。

main.py 的 _shutdown_resources() 在各资源上独立 try/except——单资源关闭
失败不阻断其余资源关闭。此处以 fake 驱动/Redis/engine 驱动关闭路径，
验证关闭顺序与「单点失败继续」语义。
"""

import pytest

from app.main import _shutdown_resources


class _AsyncFakeDriver:
    """fake AsyncDriver：close() 为协程。"""

    def __init__(self, fail=False):
        self.closed = False
        self.fail = fail

    async def close(self):
        if self.fail:
            raise RuntimeError("async driver close failed")
        self.closed = True


class _SyncFakeDriver:
    """fake sync Driver：close() 为同步方法。"""

    def __init__(self, fail=False):
        self.closed = False
        self.fail = fail

    def close(self):
        if self.fail:
            raise RuntimeError("sync driver close failed")
        self.closed = True


class _FakeRedis:
    def __init__(self, fail=False):
        self.closed = False
        self.fail = fail

    async def aclose(self):
        if self.fail:
            raise RuntimeError("redis aclose failed")
        self.closed = True


class _FakeEngine:
    def __init__(self, fail=False):
        self.disposed = False
        self.fail = fail

    async def dispose(self):
        if self.fail:
            raise RuntimeError("engine dispose failed")
        self.disposed = True


@pytest.mark.asyncio
async def test_shutdown_closes_both_drivers(monkeypatch):
    """关闭路径应 await async_neo4j_driver、调用 neo4j_driver、aclose redis、dispose engine。"""
    from app.core import database as db_mod

    async_driver = _AsyncFakeDriver()
    sync_driver = _SyncFakeDriver()
    redis = _FakeRedis()
    engine = _FakeEngine()
    monkeypatch.setattr(db_mod, "async_neo4j_driver", async_driver)
    monkeypatch.setattr(db_mod, "neo4j_driver", sync_driver)
    monkeypatch.setattr(db_mod, "redis_client", redis)
    monkeypatch.setattr(db_mod, "engine", engine)

    await _shutdown_resources()

    assert async_driver.closed, "async_neo4j_driver 应被 await close"
    assert sync_driver.closed, "neo4j_driver 应被 close"
    assert redis.closed, "redis_client 应被 aclose"
    assert engine.disposed, "engine 连接池应被 dispose"


@pytest.mark.asyncio
async def test_shutdown_continues_after_single_failure(monkeypatch):
    """单资源关闭失败不应阻断其余资源（各资源独立 try/except）。"""
    from app.core import database as db_mod

    async_driver = _AsyncFakeDriver(fail=True)
    sync_driver = _SyncFakeDriver()
    redis = _FakeRedis()
    engine = _FakeEngine()
    monkeypatch.setattr(db_mod, "async_neo4j_driver", async_driver)
    monkeypatch.setattr(db_mod, "neo4j_driver", sync_driver)
    monkeypatch.setattr(db_mod, "redis_client", redis)
    monkeypatch.setattr(db_mod, "engine", engine)

    await _shutdown_resources()  # 不抛异常，其余资源继续关闭

    assert sync_driver.closed
    assert redis.closed
    assert engine.disposed


# ── H2 修复：生产姿态 fail-fast 校验 ──

class _PatchableSettings:
    """模拟 settings：暴露 lifespan 校验读到的字段，便于 monkeypatch 单点翻转。"""

    def __init__(self, *, app_env, debug, secret_key, admin_password, cors_origins):
        self.app_env = app_env
        self.debug = debug
        self.secret_key = secret_key
        self.admin_password = admin_password
        self.cors_origins = cors_origins

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@pytest.mark.asyncio
async def test_lifespan_production_fails_fast_on_debug(monkeypatch):
    """生产环境 DEBUG=True 时应拒绝启动（SQL echo 泄 PII 至日志，H2）。"""
    from app import main as main_mod

    settings = _PatchableSettings(
        app_env="production",
        debug=True,  # 违规
        secret_key="not-default",
        admin_password="not-default",
        cors_origins=["https://app.example.com"],
    )
    monkeypatch.setattr(main_mod, "settings", settings)
    # 跳过重量级预热，仅验证启动校验
    async def _noop_prewarm():
        return None

    monkeypatch.setattr(main_mod, "_prewarm_semantic", _noop_prewarm)

    with pytest.raises(RuntimeError, match="DEBUG=True"):
        async with main_mod.lifespan(None):  # type: ignore[arg-type]
            pass  # launch


@pytest.mark.asyncio
async def test_lifespan_production_fails_fast_on_cors_wildcard(monkeypatch):
    """生产环境 CORS 通配 * 时应拒绝启动（任意站点跨域携凭据，H2）。"""
    from app import main as main_mod

    settings = _PatchableSettings(
        app_env="production",
        debug=False,
        secret_key="not-default",
        admin_password="not-default",
        cors_origins=["*"],  # 违规
    )
    monkeypatch.setattr(main_mod, "settings", settings)

    async def _noop_prewarm():
        return None

    monkeypatch.setattr(main_mod, "_prewarm_semantic", _noop_prewarm)

    with pytest.raises(RuntimeError, match="通配 \\*"):
        async with main_mod.lifespan(None):  # type: ignore[arg-type]
            pass  # launch


@pytest.mark.asyncio
async def test_lifespan_production_missing_preamble_secrets(monkeypatch):
    """生产环境未换 SECRET_KEY/ADMIN_PASSWORD 仍应拒绝启动（既有校验回归）。"""
    from app import main as main_mod

    settings = _PatchableSettings(
        app_env="production",
        debug=False,
        secret_key="change-me-in-production",  # 违规默认值
        admin_password="not-default",
        cors_origins=["https://app.example.com"],
    )
    monkeypatch.setattr(main_mod, "settings", settings)

    async def _noop_prewarm():
        return None

    monkeypatch.setattr(main_mod, "_prewarm_semantic", _noop_prewarm)

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        async with main_mod.lifespan(None):  # type: ignore[arg-type]
            pass  # launch
