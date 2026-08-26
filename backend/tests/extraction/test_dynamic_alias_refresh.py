"""动态别名表加载/刷新机制测试（第六轮审查 P0-1 回归锁）。

历史缺陷：reload_dynamic_aliases 内 asyncio.run 在运行中的事件循环调用
必抛 RuntimeError 且被 except 静默吞掉——approve 端点/任何 async 上下文里
缓存恒空、无任何可见信号。本文件锁定三条：
1. refresh_dynamic_aliases（async）在事件循环内可正确加载并更新模块缓存；
2. reload_dynamic_aliases（同步包装）在事件循环内显式抛错而非静默降级；
3. DB 故障时 fail-soft 且保持既有缓存（warning 可观测）。
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.services.extraction import dictionary as dict_mod


def _row(variant: str, standard: str) -> SimpleNamespace:
    return SimpleNamespace(variant=variant, standard_name=standard)


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def scalars(self, stmt):
        return SimpleNamespace(all=lambda: self._rows)


class _FakeFactory:
    def __init__(self, rows):
        self._rows = rows

    def __call__(self):
        return _FakeSession(self._rows)


class _BrokenFactory:
    def __call__(self):
        raise ConnectionError("db down")


class TestRefreshDynamicAliases:
    def test_refresh_loads_lowercase_mapping_into_cache(self, monkeypatch):
        monkeypatch.setattr(dict_mod, "_DYNAMIC_ALIAS_LOWER", {"old": "Old"})
        monkeypatch.setattr(
            "app.core.database.async_session_factory",
            _FakeFactory([_row(" JS ", "JavaScript"), _row(".net framework", ".NET")]),
        )
        loaded = asyncio.run(dict_mod.refresh_dynamic_aliases())
        assert loaded == 2
        cache = dict_mod.dynamic_alias_lower()
        assert cache == {"js": "JavaScript", ".net framework": ".NET"}

    def test_refresh_normalize_skill_consumes_dynamic_layer(self, monkeypatch):
        """端到端语义：approved 别名经 refresh 后被 normalize_skill 命中。"""
        monkeypatch.setattr(dict_mod, "_DYNAMIC_ALIAS_LOWER", {})
        monkeypatch.setattr(
            "app.core.database.async_session_factory",
            _FakeFactory([_row("js_lang", "JavaScript")]),
        )
        asyncio.run(dict_mod.refresh_dynamic_aliases())
        assert dict_mod.normalize_skill("js_lang") == "JavaScript"

    def test_refresh_db_failure_keeps_stale_cache(self, monkeypatch):
        monkeypatch.setattr(dict_mod, "_DYNAMIC_ALIAS_LOWER", {"js": "JavaScript"})
        monkeypatch.setattr("app.core.database.async_session_factory", _BrokenFactory())
        loaded = asyncio.run(dict_mod.refresh_dynamic_aliases())
        assert loaded == 1
        assert dict_mod.dynamic_alias_lower() == {"js": "JavaScript"}


class TestReloadSyncWrapper:
    def test_reload_raises_inside_running_loop(self):
        """事件循环内调用同步包装必须显式抛错（旧版 asyncio.run 死链防回归）。"""
        async def _call_in_loop():
            return dict_mod.reload_dynamic_aliases()

        with pytest.raises(RuntimeError, match="refresh_dynamic_aliases"):
            asyncio.run(_call_in_loop())

    def test_reload_delegates_outside_loop(self, monkeypatch):
        monkeypatch.setattr(
            "app.core.database.async_session_factory",
            _FakeFactory([_row("py", "Python")]),
        )
        loaded = dict_mod.reload_dynamic_aliases()
        assert loaded == 1
        assert dict_mod.dynamic_alias_lower() == {"py": "Python"}
