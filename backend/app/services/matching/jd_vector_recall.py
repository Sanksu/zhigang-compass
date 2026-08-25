"""向量预筛召回（阶段 C 性能修复：JD 技能池化向量粗筛 → Top-K 精评）。

阶段 C 全量 JD 评分的 36s 瓶颈在逐条 SBERT 语义评分（300 候选 × ~0.12s）。
本模块用「JD 技能名池化向量」做廉价余弦召回（9,912 × 384 一次矩阵点积，
毫秒级），把进入完整评分的候选压到 K（默认 50，50×0.12s≈6s）。

池化语义（与拍板方案一致，非 jd_embeddings 标题向量——标题向量是「岗位名
语义」与「技能集匹配」错位）：
- JD 池化向量 = 该 JD must+nice 技能名 SBERT 向量的平均（384 维）
- 候选人向量 = 简历技能名 SBERT 向量的平均

缓存：全量 JD 池化向量经 Redis 缓存（JSON 序列化，键含指纹——指纹=全部
(jd_id, 技能名集) 的哈希，JD 抽取集变化即整体失效重建），避免每请求重算
9,912 条池化（SBERT warm 后取平均仍需秒级）。Redis 不可用降级进程内重建
（一次预热后复用）。

SBERT 不可用（模型加载失败）→ 返回 None，调用方降级 rough_select 技能
命中粗选（旧路径，行为不劣化）。
"""

import hashlib
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Redis 缓存键版本（池化语义变更时递增）
_POOL_CACHE_VERSION = "v1"
_POOL_CACHE_KEY_PREFIX = "matching:jd_pool_vec:" + _POOL_CACHE_VERSION
_POOL_CACHE_TTL = 7 * 24 * 60 * 60  # 7 天（指纹变化自然失效，TTL 仅兜底）


def pool_profiles_fingerprint(profiles) -> str:
    """JD 技能集指纹（缓存键成分）：(jd_id, 排序后技能名集) 哈希。

    任何 JD 的技能集变化 → 指纹变 → 缓存整体失效重建。
    """
    h = hashlib.sha256()
    for p in profiles:
        skills = sorted(
            {r.skill_name for r in (*p.must_skills, *p.nice_skills) if r.skill_name}
        )
        h.update(f"{p.position_id}|{','.join(skills)}\n".encode("utf-8"))
    return h.hexdigest()


def build_pool_vectors(profiles, embedder) -> Optional[dict]:
    """批量构建 JD 池化向量（jd_id → 384 维 list）。

    先 warm 全量技能名（一次 batch encode，此后 _vec 全命中），再逐 JD 取
    技能向量平均。warm 失败是静默的（内部捕获）——以「warm 后缓存无任何
    所需技能」判定模型不可用，返回 None（调用方降级）。
    """
    import numpy as np

    all_names = sorted({
        r.skill_name
        for p in profiles
        for r in (*p.must_skills, *p.nice_skills)
        if r.skill_name
    })
    if not all_names:
        return {} if profiles else {}
    embedder.warm(all_names)
    cache = embedder._cache
    if not any(n in cache for n in all_names):
        logger.warning("[jd_vector_recall] SBERT 不可用（warm 后缓存为空），降级技能命中粗选")
        return None

    vecs: dict[str, list] = {}
    for p in profiles:
        names = {r.skill_name for r in (*p.must_skills, *p.nice_skills) if r.skill_name}
        known = [cache[n.strip()] for n in names if n.strip() in cache]
        if not known:
            continue
        pool = np.mean(np.stack([np.asarray(v, dtype=np.float32) for v in known]), axis=0)
        vecs[p.position_id] = [float(x) for x in pool]
    return vecs


async def load_pool_vectors_cached(profiles, embedder, redis_client=None) -> Optional[dict]:
    """带 Redis 缓存的池化向量加载（指纹命中直接返回；未命中构建并写缓存）。

    Redis 读写在调用方事件循环（客户端绑定循环）；CPU 密集段（SBERT warm +
    池化构建，首次可达数十秒）经 to_thread 进线程池不阻塞循环。
    返回 None 表示 SBERT 不可用（调用方降级 rough_select）。
    """
    import asyncio

    if not profiles:
        return {}
    fingerprint = pool_profiles_fingerprint(profiles)
    key = _POOL_CACHE_KEY_PREFIX + fingerprint

    if redis_client is not None:
        try:
            raw = await redis_client.get(key)
            if raw:
                cached = json.loads(raw)
                if isinstance(cached, dict) and cached:
                    return cached
        except Exception as e:
            logger.warning("[jd_vector_recall] 池化缓存读取失败（降级重建）: %s", e)

    vecs = await asyncio.to_thread(build_pool_vectors, profiles, embedder)
    if vecs is None:
        return None

    if redis_client is not None and vecs:
        try:
            await redis_client.set(key, json.dumps(vecs), ex=_POOL_CACHE_TTL)
        except Exception as e:
            logger.warning("[jd_vector_recall] 池化缓存写入失败（不影响本次）: %s", e)
    return vecs


def candidate_vector(skill_names: list, embedder) -> Optional[list]:
    """候选人技能池化向量（技能名向量平均）；SBERT 不可用返回 None。"""
    import numpy as np

    names = [n.strip() for n in skill_names if n and n.strip()]
    if not names:
        return None
    try:
        embedder.warm(names)
    except Exception:
        return None
    cache = embedder._cache
    known = [cache[n] for n in names if n in cache]
    if not known:
        return None
    pool = np.mean(np.stack([np.asarray(v, dtype=np.float32) for v in known]), axis=0)
    return [float(x) for x in pool]


def vector_recall(
    profiles,
    pool_vectors: dict,
    cand_vec: list,
    k: int,
) -> list:
    """余弦召回 Top-K（矩阵化点积，9,912×384 毫秒级）。

    profiles 中无池化向量的 JD（技能集空/未编码）不参与召回。
    返回按相似度降序的 profile 列表（长度 ≤ k）。
    """
    import numpy as np

    scored: list[tuple[float, object]] = []
    cand = np.asarray(cand_vec, dtype=np.float32)
    cand_norm = float(np.linalg.norm(cand))
    if cand_norm == 0:
        return []
    for p in profiles:
        vec = pool_vectors.get(p.position_id)
        if not vec:
            continue
        v = np.asarray(vec, dtype=np.float32)
        norm = float(np.linalg.norm(v))
        if norm == 0:
            continue
        scored.append((float(np.dot(cand, v) / (cand_norm * norm)), p))
    scored.sort(key=lambda t: -t[0])
    return [p for _, p in scored[:k]]
