"""测试会话结束时回收已加载数据库模块的连接资源。"""

import asyncio
import sys
from types import SimpleNamespace

import conftest
import pytest


class _AsyncResource:
    def __init__(self, error: Exception | None = None):
        self.closed = False
        self.error = error

    async def close(self) -> None:
        if self.error is not None:
            raise self.error
        self.closed = True

    async def aclose(self) -> None:
        await self.close()

    async def dispose(self) -> None:
        await self.close()


class _SyncResource:
    def __init__(self, error: Exception | None = None):
        self.closed = False
        self.error = error

    def close(self) -> None:
        if self.error is not None:
            raise self.error
        self.closed = True


def _database(
    async_neo4j_driver: _AsyncResource | None = None,
    neo4j_driver: _SyncResource | None = None,
    redis_client: _AsyncResource | None = None,
    engine: _AsyncResource | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        async_neo4j_driver=async_neo4j_driver or _AsyncResource(),
        neo4j_driver=neo4j_driver or _SyncResource(),
        redis_client=redis_client or _AsyncResource(),
        engine=engine or _AsyncResource(),
    )


class _TerminalReporter:
    def __init__(self):
        self.lines: list[str] = []

    def write_line(self, line: str) -> None:
        self.lines.append(line)


def _session(reporter: _TerminalReporter | None = None, exitstatus: pytest.ExitCode = pytest.ExitCode.OK):
    pluginmanager = SimpleNamespace(get_plugin=lambda name: reporter if name == "terminalreporter" else None)
    return SimpleNamespace(
        config=SimpleNamespace(pluginmanager=pluginmanager),
        exitstatus=exitstatus,
    )


def test_session_cleanup_does_not_import_database(monkeypatch):
    """未被测试加载的数据库模块不能由 sessionfinish 导入。"""
    monkeypatch.delitem(sys.modules, "app.core.database", raising=False)

    conftest.pytest_sessionfinish(_session(), exitstatus=pytest.ExitCode.OK)

    assert "app.core.database" not in sys.modules


def test_close_database_resources_closes_all_resources():
    """已加载模块的四类资源均按生产生命周期语义关闭。"""
    database = _database()

    errors = asyncio.run(conftest._close_database_resources(database))

    assert errors == []
    assert database.async_neo4j_driver.closed
    assert database.neo4j_driver.closed
    assert database.redis_client.closed
    assert database.engine.closed


def test_close_database_resources_continues_after_failure():
    """一个资源关闭失败时，其余资源仍必须完成关闭。"""
    error = RuntimeError("driver unavailable")
    async_driver = _AsyncResource(error)
    database = _database(async_neo4j_driver=async_driver)

    errors = asyncio.run(conftest._close_database_resources(database))

    assert errors == [("async_neo4j_driver", error)]
    assert not async_driver.closed
    assert database.neo4j_driver.closed
    assert database.redis_client.closed
    assert database.engine.closed


def test_session_cleanup_failure_reports_and_fails_successful_session(monkeypatch):
    """清理失败应写入终端报告，并将成功会话标记为测试失败。"""
    error = RuntimeError("driver unavailable")
    reporter = _TerminalReporter()
    session = _session(reporter)
    monkeypatch.setitem(sys.modules, "app.core.database", _database(async_neo4j_driver=_AsyncResource(error)))

    conftest.pytest_sessionfinish(session, exitstatus=pytest.ExitCode.OK)

    assert reporter.lines == ["ERROR: failed to close async_neo4j_driver: RuntimeError('driver unavailable')"]
    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED


def test_session_cleanup_failure_preserves_existing_failure(monkeypatch):
    """清理失败不得覆盖测试自身已产生的退出状态。"""
    session = _session(exitstatus=pytest.ExitCode.INTERRUPTED)
    monkeypatch.setitem(
        sys.modules,
        "app.core.database",
        _database(async_neo4j_driver=_AsyncResource(RuntimeError("driver unavailable"))),
    )

    conftest.pytest_sessionfinish(session, exitstatus=pytest.ExitCode.INTERRUPTED)

    assert session.exitstatus == pytest.ExitCode.INTERRUPTED
