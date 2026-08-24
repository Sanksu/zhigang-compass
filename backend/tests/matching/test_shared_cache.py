"""岗位画像共享缓存（shared_cache）回归测试。

覆盖：指针命中 / miss 构建并发布 / 同进程并发单次构建 / 损坏载荷重建 /
权重版本变化新建键 / Redis 故障降级进程 TTL / 序列化往返 / schema 版本拒绝。
"""

import asyncio
import json

import pytest

from app.services.matching.schemas import Necessity, PositionProfile, SkillRequirement
from app.services.matching.shared_cache import (
    _PAYLOAD_PREFIX,
    _POINTER_KEY,
    _SCHEMA_REVISION,
    _parse_payload,
    _serialize,
    load_positions_shared,
    weights_revision,
)


class _FakeRedis:
    """redis.asyncio 最小桩（get/set nx ex/delete/eval）。"""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.get_calls = 0

    async def get(self, key: str):
        self.get_calls += 1
        return self.store.get(key)

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def delete(self, key: str):
        return self.store.pop(key, None) is not None

    async def eval(self, script: str, numkeys: int, key: str, token: str):
        if self.store.get(key) == token:
            del self.store[key]
            return 1
        return 0


class _BoomRedis:
    """get 恒抛异常的 Redis 桩（故障降级路径）。"""

    async def get(self, key: str):
        raise ConnectionError("redis down")


def _profiles() -> list[PositionProfile]:
    return [
        PositionProfile(
            position_id="p1",
            name="后端工程师",
            must_skills=[
                SkillRequirement(
                    skill_id="s1", skill_name="Java", necessity=Necessity.MUST, weight=0.9
                )
            ],
            nice_skills=[
                SkillRequirement(
                    skill_id="s2", skill_name="Docker", necessity=Necessity.NICE, weight=0.4
                )
            ],
        )
    ]


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """每个测试重置模块级进程缓存并固定版本哈希。"""
    import app.services.matching.shared_cache as sc

    sc._local_cache.clear()
    monkeypatch.setattr(sc, "weights_revision", lambda: "w-rev-1")
    warmed: list[list[str]] = []

    def _record_warm(positions):
        warmed.append(
            [s.skill_name for p in positions for s in (*p.must_skills, *p.nice_skills)]
        )

    monkeypatch.setattr(sc, "_warm_async", _record_warm)
    yield {"warmed": warmed}


def _seed_payload(redis: _FakeRedis, profiles=None, schema_rev: str | None = None) -> str:
    """预置指针 + 载荷，返回载荷键。

    schema_rev 缺省跟随模块当前版本（真实命中路径）；显式传旧版本模拟
    跨版本部署残留（载荷与指针均改写为该版本，验证拒绝重建）。
    """
    if schema_rev is None:
        schema_rev = _SCHEMA_REVISION
    profiles = profiles if profiles is not None else _profiles()
    graph_rev = "g-rev-1"
    weights_rev = "w-rev-1"
    key = f"{_PAYLOAD_PREFIX}{graph_rev}:{weights_rev}"
    payload = _serialize(profiles, graph_rev, weights_rev)
    if schema_rev != _SCHEMA_REVISION:
        payload = payload.replace(
            f'"schema_revision":"{_SCHEMA_REVISION}"', f'"schema_revision":"{schema_rev}"'
        )
    redis.store[key] = payload
    redis.store[_POINTER_KEY] = (
        '{"key":"%s","schema_revision":"%s","graph_revision":"%s",'
        '"weights_revision":"%s","published_at":"2026-08-17T00:00:00+00:00"}'
        % (key, schema_rev, graph_rev, weights_rev)
    )
    return key


def test_pointer_hit_returns_parsed_profiles(_reset_state, monkeypatch):
    redis = _FakeRedis()
    _seed_payload(redis)
    loads = []

    def _fake_uncached():
        loads.append(1)
        return _profiles()

    monkeypatch.setattr("app.services.matching.loaders._load_positions_uncached", _fake_uncached)

    positions = asyncio.run(load_positions_shared(redis=redis))

    assert len(positions) == 1
    assert positions[0].name == "后端工程师"
    assert [s.skill_name for s in positions[0].must_skills] == ["Java"]
    assert loads == []  # 指针命中不触发图谱加载
    assert _reset_state["warmed"] == [["Java", "Docker"]]  # 反序列化后进程内预热


def test_miss_builds_publishes_and_second_call_hits(_reset_state, monkeypatch):
    redis = _FakeRedis()
    loads = []

    def _fake_uncached():
        loads.append(1)
        return _profiles()

    monkeypatch.setattr("app.services.matching.loaders._load_positions_uncached", _fake_uncached)

    positions = asyncio.run(load_positions_shared(redis=redis))
    assert len(positions) == 1
    assert len(loads) == 1

    # 构建后：载荷键 + 指针已发布
    meta = json.loads(redis.store[_POINTER_KEY])
    key = meta["key"]
    assert key.startswith(_PAYLOAD_PREFIX)
    assert key in redis.store

    # 第二次调用：指针命中 + 进程内缓存，不再次加载
    asyncio.run(load_positions_shared(redis=redis))
    assert len(loads) == 1


def test_concurrent_callers_single_build(_reset_state, monkeypatch):
    redis = _FakeRedis()
    loads = []
    import time as _time

    def _slow_uncached():
        _time.sleep(0.05)
        loads.append(1)
        return _profiles()

    monkeypatch.setattr("app.services.matching.loaders._load_positions_uncached", _slow_uncached)

    async def run():
        return await asyncio.gather(
            load_positions_shared(redis=redis),
            load_positions_shared(redis=redis),
            load_positions_shared(redis=redis),
        )

    results = asyncio.run(run())
    assert all(len(r) == 1 for r in results)
    assert len(loads) == 1  # 同进程并发只构建一次


def test_corrupt_payload_rebuilds(_reset_state, monkeypatch):
    redis = _FakeRedis()
    key = _seed_payload(redis)
    redis.store[key] = "{not-json"
    loads = []

    def _fake_uncached():
        loads.append(1)
        return _profiles()

    monkeypatch.setattr("app.services.matching.loaders._load_positions_uncached", _fake_uncached)

    positions = asyncio.run(load_positions_shared(redis=redis))
    assert len(positions) == 1
    assert len(loads) == 1  # 损坏载荷触发重建


def test_schema_revision_mismatch_rebuilds(_reset_state, monkeypatch):
    redis = _FakeRedis()
    _seed_payload(redis, schema_rev="v1")
    loads = []

    def _fake_uncached():
        loads.append(1)
        return _profiles()

    monkeypatch.setattr("app.services.matching.loaders._load_positions_uncached", _fake_uncached)

    positions = asyncio.run(load_positions_shared(redis=redis))
    assert len(positions) == 1
    assert len(loads) == 1  # schema 版本不匹配视为 miss


def test_weights_revision_change_builds_new_key(_reset_state, monkeypatch):
    import app.services.matching.shared_cache as sc

    redis = _FakeRedis()
    _seed_payload(redis)
    loads = []

    def _fake_uncached():
        loads.append(1)
        return _profiles()

    monkeypatch.setattr("app.services.matching.loaders._load_positions_uncached", _fake_uncached)
    monkeypatch.setattr(sc, "weights_revision", lambda: "w-rev-2")

    positions = asyncio.run(load_positions_shared(redis=redis))
    assert len(positions) == 1
    assert len(loads) == 1  # 权重版本变化 → 新键重建
    # 旧载荷键保留可读，新载荷键已发布（两个版本并存）
    keys = list(redis.store)
    assert any("w-rev-1" in k for k in keys) and any("w-rev-2" in k for k in keys)


def test_redis_error_degrades_to_local_loader(_reset_state, monkeypatch):
    boom = _BoomRedis()
    loads = []

    def _fake_local():
        loads.append(1)
        return _profiles()

    monkeypatch.setattr("app.services.matching.loaders.load_positions_from_graph", _fake_local)

    positions = asyncio.run(load_positions_shared(redis=boom))
    assert len(positions) == 1
    assert len(loads) == 1  # Redis 故障 → 降级进程 TTL 加载


def test_serialization_roundtrip():
    raw = _serialize(_profiles(), "g-rev-1", "w-rev-1")
    parsed = _parse_payload(raw)
    assert parsed == _profiles()


def test_weights_revision_is_deterministic():
    assert weights_revision() == weights_revision()


def test_empty_positions_not_published(_reset_state, monkeypatch):
    """空图/加载空结果：本次返回空，但不发布载荷与指针（防空载荷 7 天污染）。"""
    redis = _FakeRedis()
    loads = []

    def _fake_uncached():
        loads.append(1)
        return []

    monkeypatch.setattr("app.services.matching.loaders._load_positions_uncached", _fake_uncached)

    positions = asyncio.run(load_positions_shared(redis=redis))

    assert positions == []
    assert _POINTER_KEY not in redis.store
    assert not any(k.startswith(_PAYLOAD_PREFIX) for k in redis.store)
    assert len(loads) == 1


def test_poisoned_empty_payload_self_heals(_reset_state, monkeypatch):
    """历史空载荷（部署窗口污染实证）不再命中：读路径拒绝 → 重建并发布真实载荷。"""
    redis = _FakeRedis()
    old_key = _seed_payload(redis, profiles=[])
    loads = []

    def _fake_uncached():
        loads.append(1)
        return _profiles()

    monkeypatch.setattr("app.services.matching.loaders._load_positions_uncached", _fake_uncached)

    positions = asyncio.run(load_positions_shared(redis=redis))

    assert len(positions) == 1
    meta = json.loads(redis.store[_POINTER_KEY])
    assert meta["key"] != old_key  # 指针已切到真实载荷
    assert len(loads) == 1
