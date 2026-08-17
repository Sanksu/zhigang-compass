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
