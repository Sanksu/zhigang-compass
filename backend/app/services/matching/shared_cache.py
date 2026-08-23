"""岗位画像共享缓存（P1）：Redis 版本化载荷 + 跨进程单飞 + 进程内合并。

背景：/recommend 与 /compare 全量加载岗位画像（2 条 Neo4j 查询 + SBERT 批量
预热），API 与 worker 是独立进程，各自进程 TTL 会重复冷加载；多 worker 扩容时
同版本重复全图扫描 + 预热，线程池与 Neo4j 压力翻倍。

设计（不可变版本化载荷，先写后切）：
- 指针键 matching:positions:current → {key, graph_revision, weights_revision}
- 载荷键 matching:positions:v2:{graph_hash}:{weights_hash}（不可变，旧版本不删，
  进行中请求可继续读旧版本）
- graph_hash = 岗位画像数据规范化哈希（对实际加载结果自描述，不依赖 ETL 单发哈希）
- weights_hash = 匹配权重配置规范化哈希（load_weights + sim_threshold 实际生效值）
- 构建锁 matching:positions:build:{weights_hash}（SET NX EX 120s）：赢家
  加载 → 写载荷 → 切指针；输家限时轮询指针；进程内 asyncio.Lock 合并同进程并发
- Redis 故障显式降级到进程 TTL 加载器（load_positions_from_graph），不静默返回空
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from datetime import datetime, timezone

from app.services.matching.schemas import PositionProfile

logger = logging.getLogger(__name__)

# v3（2026-08-22）：软技能退出评分池（soft_requirements 独立通道），画像
# schema 变更——读路径校验 schema_revision，旧 v2 载荷（软技能仍在 nice）不供
_SCHEMA_REVISION = "v3"
_POINTER_KEY = "matching:positions:current"
_PAYLOAD_PREFIX = "matching:positions:v2:"
_BUILD_LOCK_PREFIX = "matching:positions:build:"
_LOCK_TTL_SECONDS = 120
_POLL_INTERVAL = 0.1
_POLL_DEADLINE = 90.0
_PAYLOAD_TTL = 7 * 24 * 3600
_POINTER_TTL = 7 * 24 * 3600

# 进程内共享缓存（按载荷键缓存反序列化结果，无 TTL——键含版本，版本变化自然失效）
_local_cache: dict[str, list[PositionProfile]] = {}
# 进程内构建锁（合并同进程并发 miss）
_process_lock = asyncio.Lock()

_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


def _redis():
    """懒取共享 Redis 客户端（测试注入 FakeRedis）。"""
    from app.core.database import redis_client

    return redis_client


def weights_revision() -> str:
    """匹配权重实际生效值（三元组 + sim_threshold）的规范化哈希。"""
    from app.services.matching.weights import load_sim_threshold, load_weights

    canonical = json.dumps(
        {"weights": list(load_weights()), "sim_threshold": load_sim_threshold()},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _positions_hash(positions: list[PositionProfile]) -> str:
    """岗位画像数据规范化哈希（自描述版本身份）。"""
    canonical = json.dumps(
        [p.model_dump(mode="json") for p in positions],
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _serialize(positions: list[PositionProfile], graph_revision: str, weights_revision: str) -> str:
    payload = {
        "schema_revision": _SCHEMA_REVISION,
        "graph_revision": graph_revision,
        "weights_revision": weights_revision,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "positions": [p.model_dump(mode="json") for p in positions],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _parse_payload(raw: str) -> list[PositionProfile] | None:
    """反序列化并校验载荷；损坏/版本不匹配返回 None（调用方重建）。"""
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema_revision") != _SCHEMA_REVISION:
        return None
    try:
        return [PositionProfile.model_validate(item) for item in payload.get("positions", [])]
    except (TypeError, ValueError):
        return None


async def _release_lock(redis, key: str, token: str) -> None:
    """token 校验释放（Lua 原子：过期锁不能被旧持有者误删）。"""
    try:
        await redis.eval(_RELEASE_LUA, 1, key, token)
    except Exception:
        try:
            if await redis.get(key) == token:
                await redis.delete(key)
        except Exception:
            pass


def _warm_async(positions: list[PositionProfile]) -> None:
    """反序列化后进程内预热语义向量（批量 encode 放线程池执行）。"""
    from app.services.matching.semantic import SkillEmbedder

    names = [
        s.skill_name
        for p in positions
        for s in (*p.must_skills, *p.nice_skills, *p.soft_requirements)
    ]
    try:
        SkillEmbedder.get().warm(names)
    except Exception:
        pass


async def _load_from_payload(redis, key: str) -> list[PositionProfile] | None:
    """读载荷 → 校验 → 进程内缓存 + 预热。"""
    cached = _local_cache.get(key)
    if cached is not None:
        return cached
    raw = await redis.get(key)
    if raw is None:
        return None
    positions = _parse_payload(raw)
    if positions is None:
        _local_cache.pop(key, None)
        return None
    _local_cache[key] = positions
    await asyncio.to_thread(_warm_async, positions)
    return positions


async def _read_pointer(redis, weights_rev: str) -> list[PositionProfile] | None:
    """指针命中且 schema/权重版本一致 → 载荷；否则 None。"""
    raw = await redis.get(_POINTER_KEY)
    if raw is None:
        return None
    try:
        meta = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    key = meta.get("key", "") if isinstance(meta, dict) else ""
    if (
        not key
        or meta.get("schema_revision") != _SCHEMA_REVISION
        or meta.get("weights_revision") != weights_rev
    ):
        return None
    positions = await _load_from_payload(redis, key)
    if positions is None:
        return None
    return positions


async def _poll_pointer(redis, weights_rev: str, deadline: float) -> list[PositionProfile] | None:
    """输家限时轮询指针（构建锁持有者崩溃时由锁 TTL 兜底）。"""
    while time.monotonic() < deadline:
        positions = await _read_pointer(redis, weights_rev)
        if positions is not None:
            return positions
        await asyncio.sleep(_POLL_INTERVAL)
    return None


async def load_positions_shared(redis=None) -> list[PositionProfile]:
    """共享缓存版岗位画像加载（先指针 → 载荷；miss → 单飞构建）。

    Redis 不可用时降级到进程 TTL 加载器（load_positions_from_graph），
    返回结果与旧行为一致，仅失去跨进程共享。
    """
    from app.services.matching.loaders import load_positions_from_graph

    r = redis or _redis()
    weights_rev = weights_revision()
    try:
        positions = await _read_pointer(r, weights_rev)
        if positions is not None:
            return positions
    except Exception as exc:
        logger.warning("共享岗位缓存不可用（Redis 异常），降级进程 TTL 加载: %s", exc)
        return await asyncio.to_thread(load_positions_from_graph)

    async with _process_lock:
        try:
            positions = await _read_pointer(r, weights_rev)
            if positions is not None:
                return positions
        except Exception as exc:
            logger.warning("共享岗位缓存二次读取失败，降级进程 TTL 加载: %s", exc)
            return await asyncio.to_thread(load_positions_from_graph)

        lock_key = _BUILD_LOCK_PREFIX + weights_rev
        token = uuid.uuid4().hex
        try:
            acquired = await r.set(lock_key, token, nx=True, ex=_LOCK_TTL_SECONDS)
        except Exception as exc:
            logger.warning("共享岗位缓存锁不可用，降级进程 TTL 加载: %s", exc)
            return await asyncio.to_thread(load_positions_from_graph)
        if not acquired:
            positions = await _poll_pointer(r, weights_rev, time.monotonic() + _POLL_DEADLINE)
            if positions is not None:
                return positions
            # 锁持有者异常退出：本次直接本地加载（重复构建由锁 TTL 收敛）
            return await asyncio.to_thread(load_positions_from_graph)

        try:
            from app.services.matching.loaders import _load_positions_uncached

            positions = await asyncio.to_thread(_load_positions_uncached)
            graph_rev = _positions_hash(positions)
            key = _PAYLOAD_PREFIX + graph_rev + ":" + weights_rev
            meta = {
                "key": key,
                "schema_revision": _SCHEMA_REVISION,
                "graph_revision": graph_rev,
                "weights_revision": weights_rev,
                "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            await r.set(key, _serialize(positions, graph_rev, weights_rev), ex=_PAYLOAD_TTL)
            await r.set(_POINTER_KEY, json.dumps(meta, ensure_ascii=False), ex=_POINTER_TTL)
            _local_cache[key] = positions
            return positions
        except Exception as exc:
            logger.exception("共享岗位缓存构建失败，降级进程 TTL 加载: %s", exc)
            return await asyncio.to_thread(load_positions_from_graph)
        finally:
            await _release_lock(r, lock_key, token)
