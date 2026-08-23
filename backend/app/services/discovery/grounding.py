"""新岗位发现阶段二：RAG 接地（设计文档 7.2.3 节）。

candidate 触发后执行两层接地：
1. 权威岗位库检索：occupations 三源（O*NET / 人社部大典 / LinkedIn，
   同一表 source 字段区分；PostgreSQL 语义向量 + Neo4j 全文双路），
   命中后取权威定义（英文）作为定义草案基座。
2. 种子列表匹配：预置 12 个新兴岗位种子（configs/emerging_seeds.yaml），
   命中后取种子描述作为定义草案基座。

权威库双路检索（§7.2.3 原文）：Neo4j 全文索引（关键词 top-10）+ pgvector
向量检索（语义 top-10），合并去重。降级链：语义路（向量列/扩展/模型任一
不可用）→ 跳过；关键词路（Neo4j 未同步/不可用）→ PostgreSQL ILIKE。

定义草案生成：LLM 可用时聚合上下文生成中文草案；不可用或失败时
回退权威库定义原文（避免草案为空阻塞 admin 审核）。

RAG 接地是"辅助确认"而非"硬门控"：未命中权威库/种子的 candidate
仍留在 candidate 池标记 unverified，由 admin 人工判断（设计文档 7.2.3）。
"""

import asyncio
import json
import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from app.services.kg.fulltext import sanitize_fulltext

import yaml
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.discovery.nli_guard import (
    SUSPICIOUS_THRESHOLD,
    detect_contradiction,
)
from app.services.discovery.schemas import RagGroundingResult

_SEEDS_PATH = Path(__file__).resolve().parents[3] / "configs" / "emerging_seeds.yaml"

# 权威库命中判定阈值：岗位名/别名任一含 candidate 岗位名核心词即视为命中。
# 简单 substring 匹配（小写），避免大小写与空格差异导致漏判。
_MATCH_MIN_ALIAS_LEN = 3

# Neo4j 全文查询（Lucene 语法）特殊字符：查询前剔除，避免语法异常

# ── 检索融合与缓存（2026-08-13 评审 P1-2 / P2）──
# RRF 融合常数 k=60（业界默认）：跨源排序融合，消除语义余弦（0-1）与
# 全文分（5-25）量纲差异——此前直接相加导致高分源垄断 top 排名。
RRF_K = 60
# Redis 检索缓存 TTL：occupations 三源仅脚本导入时变更（低频），6h 平衡
# 新鲜度与命中率；Redis 不可用/异常静默降级（RAG 是增强，不阻塞检索）。
_CACHE_TTL_SECONDS = 6 * 3600
# 测试通过 monkeypatch 关闭（避免 fake db 用例命中真实缓存）
_CACHE_ENABLED = True

# ── 降级可观测性（第五轮审查 P1-5）──
# 六条检索/接地降级路径曾全静默：语义路/Neo4j 全文/Redis/LLM 任一故障均无声
# 降级，可在生产潜伏数周不被发现。此处累计各组件降级次数并打 warning，运维
# 巡检：docker exec zhigang-api python -c "from app.services.discovery.grounding
# import degradation_counts; print(dict(degradation_counts))"
degradation_counts: Counter[str] = Counter()
logger = logging.getLogger(__name__)


def _record_degradation(component: str, error: Exception) -> None:
    """记录一次降级（计数 + warning），不改变任何控制流。"""
    degradation_counts[component] += 1
    logger.warning("grounding 降级 [%s]: %s", component, error)


def _norm(text: str) -> str:
    """归一化：小写 + 去首尾空白。"""
    return (text or "").strip().lower()




def match_seed(position_name: str, seeds: list[dict]) -> Optional[dict]:
    """匹配种子列表（name/aliases 与岗位名子串互相包含）。

    Returns:
        命中的种子 dict，未命中返回 None
    """
    pos = _norm(position_name)
    if not pos:
        return None
    for seed in seeds:
        names = [seed.get("name", ""), *(seed.get("aliases") or [])]
        for name in names:
            n = _norm(name)
            if not n:
                continue
            # 岗位名含种子名 或 种子名含岗位名（覆盖 "RAG 工程师" vs "RAG" 双向）
            if n in pos or pos in n:
                return seed
    return None


def _normalize_hit(code, name, category, definition, aliases, pos, score, source):
    """统一命中结构（语义/关键词两路同一 shape，供前端/定义草案消费）。

    score：语义路为余弦相似度，关键词路为全文分数或 name/别名命中分。
    name_hit/alias_hits：子串包含（展示用）；exact_hit：岗位名与职业名/别名
    完全一致（精确桥接，_merge_hits 置顶的依据）。
    """
    name_hit = pos in _norm(name)
    alias_hits = [a for a in (aliases or []) if pos in _norm(a)]
    exact_hit = _norm(name) == pos or any(_norm(a) == pos for a in (aliases or []))
    return {
        "code": code,
        "name": name,
        "category": category,
        "definition": definition,
        "name_hit": name_hit,
        "alias_hits": alias_hits,
        "exact_hit": exact_hit,
        "score": score,
        "source": source,
    }


def _merge_hits(hits: list[dict], limit: int) -> list[dict]:
    """按 code 合并去重，排序用跨源融合分（RRF + 精确命中置顶），取前 limit 条。

    融合排序（08-13 评审 P1-2）：语义路余弦（0-1）与关键词路全文分（5-25）
    量纲不同，直接相加会让高分源垄断；改用 RRF（Reciprocal Rank Fusion，
    k=60）按各源内 rank 融合。精确命中（name/aliases 完整包含岗位名）置顶
    ——cjk 全文分词下"工程技术人员"等泛词噪声分可达 20+，而别名精确匹配
    （如"前端开发工程师"→ 前端开发工程技术人员 别名）无加权优势会被挤出；
    置顶确保权威库别名桥接排前（JD 岗位名 → 大典职业）。

    score 字段保留各源原始值（语义余弦/全文分），融合分仅用于排序——
    消费方（诊断 RAG 的 _occupations）按 score 展示不受融合影响。
    """
    # 各源内按原始分降序取 rank（无 source 字段的按单源处理）
    by_source: dict[str, list[tuple[int, dict]]] = {}
    for i, h in enumerate(hits):
        by_source.setdefault(h.get("source") or "unknown", []).append((i, h))
    fused: dict[int, float] = {}
    for items in by_source.values():
        items.sort(key=lambda p: p[1].get("score", 0.0), reverse=True)
        for rank, (i, _) in enumerate(items, start=1):
            fused[i] = fused.get(i, 0.0) + 1.0 / (RRF_K + rank)

    def _is_exact(h: dict) -> bool:
        """精确桥接判定：岗位名与职业名/别名**完全一致**才置顶。

        不能用子串判定——"测试工程师"是"渗透测试工程师"（信息安全职业别名）
        的子串，子串置顶会把两个不同职业同时抬到顶部，原始分高者胜出，
        桥接失效（08-13 实测：测试工程师 → 信息安全工程技术人员 误判）。
        """
        return bool(h.get("exact_hit"))

    ranked_idx = sorted(
        range(len(hits)),
        key=lambda i: (
            0 if _is_exact(hits[i]) else 1,               # 精确命中置顶
            -fused[i],                                     # 融合分降序
            -float(hits[i].get("score", 0.0)),             # 同融合分保留原始高分
        ),
    )
    # 按 code 去重（融合分高者排前，天然保留最优版本）
    best: dict[str, int] = {}
    out: list[dict] = []
    for i in ranked_idx:
        h = hits[i]
        if h["code"] in best:
            continue
        best[h["code"]] = i
        out.append(h)
    return out[:limit]


def _escape_ilike(pattern: str) -> str:
    """转义 ILIKE 通配符（%/_）与转义符本身（08-15 中危修复）。

    ILIKE 的 %/_ 是通配符，用户输入含 % 或 _ 会变成模糊匹配（如搜索 "100%" 
    匹配全部、"_" 匹配单字符）——词面检索应只做字面包含匹配。
    """
    return pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _pg_ilike(db: AsyncSession, pos: str, limit: int) -> list[dict]:
    """PostgreSQL ILIKE 关键词检索（Neo4j 全文路的降级兜底）。"""
    from app.models.business import Occupation
    from sqlalchemy import cast
    from sqlalchemy.dialects import postgresql

    escaped = _escape_ilike(pos)
    stmt = (
        select(Occupation)
        .where(
            (Occupation.name.ilike(f"%{escaped}%", escape="\\"))
            | (cast(Occupation.aliases, postgresql.TEXT).ilike(f"%{escaped}%", escape="\\"))
        )
        .limit(limit)
    )
    rows = (await db.scalars(stmt)).all()
    return [
        _normalize_hit(
            occ.code, occ.name, occ.category, occ.definition, occ.aliases,
            pos, 1.0 if pos in _norm(occ.name) else 0.5, "keyword",
        )
        for occ in rows
    ]


def _query_fulltext(neo4j, q: str, limit: int) -> list[dict]:
    """同步 Neo4j 全文查询（线程池调用，异常由调用方降级 ILIKE）。"""
    with neo4j.session() as session:
        return session.run(
            "CALL db.index.fulltext.queryNodes('occupation_search', $q) "
            "YIELD node, score "
            "RETURN node.code AS code, node.name AS name, "
            "node.category AS category, node.definition AS definition, "
            "node.aliases AS aliases, score "
            "LIMIT $limit",
            q=q,
            limit=limit,
        ).data()


async def _neo4j_fulltext(neo4j, pos: str, limit: int) -> list[dict]:
    """Neo4j occupation_search 全文索引关键词检索（设计 7.2.3 双路之一）。

    neo4j 为驱动对象（.session() 上下文）。查询前剔除 Lucene 特殊字符；
    任一异常返回 []（由调用方降级 PostgreSQL ILIKE）。
    """
    q = sanitize_fulltext(pos)
    if not q:
        return []
    try:
        # 同步 Neo4j 查询放线程池，避免阻塞事件循环
        # 查询范围扩大（limit×3，至少 30）：cjk 分词下精确别名命中可能不在
        # 全文高分前列，扩大候选集供 _merge_hits 的精确加权拣出
        rows = await asyncio.to_thread(_query_fulltext, neo4j, q, max(limit * 3, 30))
    except Exception as e:
        # Neo4j 未同步/不可达：降级 ILIKE，不阻塞接地
        _record_degradation("neo4j_fulltext", e)
        return []
    return [
        _normalize_hit(
            r["code"], r["name"], r.get("category") or "",
            r.get("definition") or "", r.get("aliases") or [],
            pos, float(r["score"]), "keyword",
        )
        for r in rows
        if r.get("code")
    ]


async def _expand_fulltext_query(db: AsyncSession, pos: str) -> str:
    """岗位名 → 规范职业名扩展（08-13 评审 P2 query 扩展）。

    从 occupations 取 aliases 数组**精确等于**岗位名的职业（别名桥接），
    规范名与岗位名 OR 组合——JD 岗位名（如"前端开发工程师"）与大典规范名
    （如"计算机软件工程技术人员"）cjk 分词不同，扩展让全文路直接命中目标。
    精确数组包含（JSONB @>）而非 ILIKE 部分匹配：避免"测试工程师"扩展出
    "渗透测试工程师"（信息安全）等部分匹配职业放大检索噪声。
    查询失败/无扩展返回岗位名本身（不放大检索面）。
    """
    from app.models.business import Occupation

    try:
        rows = (
            await db.scalars(
                select(Occupation.name)
                .where(
                    (Occupation.name == pos)
                    | (Occupation.aliases.contains([pos]))
                )
                .limit(3)
            )
        ).all()
        extras = [n for n in rows if _norm(n) != pos]
        if not extras:
            return pos
        return " OR ".join([pos, *extras])
    except Exception as e:
        # 扩展查询失败回退岗位名本身（扩展是增强，失败不放大/不阻塞检索面）
        _record_degradation("query_expansion", e)
        return pos


async def _keyword_search(neo4j, db: AsyncSession, pos: str, limit: int) -> list[dict]:
    """关键词路：Neo4j 全文优先，未命中/不可用时降级 PostgreSQL ILIKE。"""
    if neo4j is not None:
        # 规范职业名扩展（ILIKE 反向查）：扩大全文命中面，目标职业无需依赖
        # 精确加权挤进 top 即可直接被命中
        q = await _expand_fulltext_query(db, pos)
        hits = await _neo4j_fulltext(neo4j, q, limit)
        if hits:
            return hits
    return await _pg_ilike(db, pos, limit)


async def _semantic_search(db: AsyncSession, pos: str, embedder, limit: int) -> list[dict]:
    """pgvector 语义检索：occupations.embedding 余弦相似度 Top-k（设计 7.2.3）。

    embedder 为 None（未注入）时跳过语义路；模型不可用抛
    SemanticUnavailableError，由调用方捕获降级为关键词路。

    score 与排序同源（08-13 评审 P1-1）：直接取 1 - cosine_distance——
    此前排序用完整文本向量（name 强调 + category + aliases + definition，
    与 import_occupations._embed_text 一致）而 score 用 embedder.similarity
    (职业名, 岗位名) 重新计算，两者不一致导致 _merge_hits 融合排名失真。
    """
    if embedder is None:
        return []
    from app.models.business import Occupation

    # SBERT 推理放线程池（embed 同步，避免阻塞事件循环）
    qvec = await asyncio.to_thread(embedder.embed, pos)
    stmt = (
        select(
            Occupation,
            (1 - Occupation.embedding.cosine_distance(qvec)).label("sim"),
        )
        .order_by(Occupation.embedding.cosine_distance(qvec))
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [
        _normalize_hit(
            occ.code, occ.name, occ.category, occ.definition, occ.aliases,
            pos, float(sim), "semantic",
        )
        for occ, sim in rows
    ]


def _cache_key(pos: str, limit: int) -> str:
    """检索缓存键：岗位名 + limit（不同 top-k 口径不互相污染）。"""
    return f"occ_search:{pos}:{limit}"


async def _cache_get(pos: str, limit: int) -> Optional[list[dict]]:
    """读检索缓存；Redis 不可用/异常/无命中返回 None（RAG 是增强，不阻塞）。"""
    if not _CACHE_ENABLED:
        return None
    try:
        from app.core.database import redis_client

        raw = await redis_client.get(_cache_key(pos, limit))
        if not raw:
            return None
        hits = json.loads(raw)
        return hits if isinstance(hits, list) else None
    except Exception as e:
        # Redis 读失败按未命中处理（下次检索回源并再次记录）
        _record_degradation("redis_cache_get", e)
        return None


async def _cache_set(pos: str, limit: int, hits: list[dict]) -> None:
    """写检索缓存（TTL 6h）；Redis 不可用静默跳过。"""
    if not _CACHE_ENABLED or not hits:
        return
    try:
        from app.core.database import redis_client

        await redis_client.set(
            _cache_key(pos, limit), json.dumps(hits, ensure_ascii=False),
            ex=_CACHE_TTL_SECONDS,
        )
    except Exception as e:
        # Redis 写失败跳过缓存（检索结果仍正确返回）
        _record_degradation("redis_cache_set", e)


async def search_authoritative(
    position_name: str,
    db: AsyncSession,
    limit: int = 10,
    *,
    neo4j=None,
    embedder=None,
    use_cache: bool = True,
) -> list[dict]:
    """在权威岗位库（occupations 三源：O*NET / 人社部大典 / LinkedIn）检索候选岗位。

    检索方法：pgvector 语义 top-k（occupations.embedding 余弦）+ Neo4j 全文
    关键词 top-k（occupation_search 索引），按 code 合并去重取前 limit 条。
    三源数据均落在同一 occupations 表（source 字段区分），一次检索天然覆盖
    全部来源。降级链：语义路（向量列/扩展/模型任一不可用）→ 跳过；关键词路
    （Neo4j 未同步/不可用）→ PostgreSQL ILIKE。接地是"辅助确认"而非硬门控，
    任一检索源失败不阻塞判定。

    Args:
        position_name: candidate 岗位名
        db: PostgreSQL 会话（权威库检索）
        limit: 返回条数上限（默认 10，对齐设计文档 7.2.3 的 top-10 口径）
        neo4j: Neo4j 驱动（默认 None → 跳过全文路，仅 ILIKE 关键词路）
        embedder: SkillEmbedder（默认 None → 跳过语义路）
        use_cache: Redis 检索缓存（默认开；评测脚本传 False 测真实检索质量）

    Returns:
        命中的 occupation dict 列表（code/name/category/definition/score）
    """
    pos = _norm(position_name)
    if not pos:
        return []
    if len(pos) < _MATCH_MIN_ALIAS_LEN:
        return []

    # Redis 检索缓存（评审 P2）：occupations 三源低频变更，命中直接返回
    # （embedder 前向推理与 Neo4j 全文是最贵的两步，缓存整体跳过后延迟显著下降）
    if use_cache:
        cached = await _cache_get(pos, limit)
        if cached is not None:
            return cached

    semantic_hits = []
    try:
        semantic_hits = await _semantic_search(db, pos, embedder, limit)
    except Exception as e:
        # 向量列缺失/扩展不可用/模型不可用 → 语义路降级为关键词路
        _record_degradation("semantic_search", e)
    keyword_hits = await _keyword_search(neo4j, db, pos, limit)
    merged = _merge_hits([*semantic_hits, *keyword_hits], limit)
    if use_cache:
        await _cache_set(pos, limit, merged)
    return merged


_DEFINITION_SYSTEM_PROMPT = """你是岗位定义专家。根据岗位名称与参考信息，\
用中文撰写简洁的岗位定义草案（1-3 句话）。参考信息可能是英文 O*NET 定义，\
请翻译并结合岗位名凝练成中文。只输出定义本身，不要前缀如"岗位定义："。"""

_DEFINITION_TASK_TEMPLATE = """岗位名称：{position_name}

参考信息（英文权威定义，请翻译并凝练）：
{reference}

输出中文岗位定义草案："""


class _DefinitionDraft(BaseModel):
    """LLM 定义草案输出约束（幻觉防控第一道防线：JSON Schema 强校验）。"""

    text: str


@dataclass
class _DefinitionResult:
    """定义草案生成结果。

    text: 最终定义草案
    source: 产出来源（llm=LLM 生成；occupation/seed=参考原文兜底）
    nli_contradicted: 是否因 NLI 矛盾检测被截断回退参考原文（软门控打标）
    """

    text: str
    source: str = "reference"
    nli_contradicted: bool = False


# NLI 软门控重采样指令：首稿被 NLI 判为可疑/矛盾时，要求 LLM 严格对齐
# 参考信息重写（不引入参考中不存在的否定表述或降级的学历/经验要求）
_RESAMPLE_SYSTEM_PROMPT = """你是岗位定义专家。你上次生成的岗位定义草案与参考信息
存在矛盾，请重新基于参考信息改写，忠实反映参考内容：不要引入参考信息中不存在的
否定表述，不得降低参考中的学历/经验要求，也不要添加参考未提及的硬性门槛。
只输出定义本身，不要前缀如"岗位定义："。"""


async def _generate_definition(
    position_name: str,
    seed: Optional[dict],
    occupation: Optional[dict],
    llm,
) -> _DefinitionResult:
    """生成岗位定义草案（含 NLI 矛盾检测软门控，P0）。

    流程：LLM 生成（可配置）→ NLI 检测 vs 参考基座（图谱/权威库检索结果）
    → 无矛盾放行 / 可疑触发一次重采样（对齐参考重写）→ 确认矛盾或重采样
    后仍可疑 → 截断回退参考原文 + 打标 nli_contradicted。
    LLM 失败静默回退原文，不阻塞接地判定（RAG 接地是"辅助确认"而非硬门控）。
    """
    reference = ""
    source = "reference"
    if occupation and occupation.get("definition"):
        reference = occupation["definition"]
        source = "occupation"
    elif seed and seed.get("description"):
        reference = seed["description"]
        source = "seed"

    if not reference:
        return _DefinitionResult(text="", source="")

    if llm is not None:
        # 首稿 + 重采样至多各一次：NLI 软门控触发重采样（不重复调用，防放大成本）
        for attempt in range(2):
            try:
                draft = await asyncio.to_thread(
                    llm.extract_structured,
                    _DEFINITION_TASK_TEMPLATE.format(
                        position_name=position_name, reference=reference
                    ),
                    _DefinitionDraft,
                    system_prompt=(
                        _DEFINITION_SYSTEM_PROMPT
                        if attempt == 0
                        else _RESAMPLE_SYSTEM_PROMPT
                    ),
                )
                text = (draft.text or "").strip()
                if not text:
                    break
                result = detect_contradiction(reference, text)
                if result.label == "contradiction":
                    # 确认矛盾 → 强制截断回退参考原文 + 打标（软门控，不再重采样）
                    return _DefinitionResult(
                        text=reference, source=source, nli_contradicted=True
                    )
                if result.score < SUSPICIOUS_THRESHOLD:
                    # 无矛盾信号（entailment/neutral）→ 放行
                    return _DefinitionResult(text=text, source="llm")
                if attempt == 0:
                    # 可疑（未达确认级）→ 触发一次重采样（软门控第一级）
                    continue
                # 重采样后仍可疑 → 截断回退参考原文 + 打标（软门控第二级）
                return _DefinitionResult(
                    text=reference, source=source, nli_contradicted=True
                )
            except Exception as e:
                # LLM 失败回退到原文，不阻塞接地判定（计数+告警）
                _record_degradation("llm_definition", e)
                break

    # 回退：权威库定义原文（英文）或种子描述
    return _DefinitionResult(text=reference, source=source)


async def ground_with_rag(
    position_name: str,
    db: AsyncSession,
    *,
    llm=None,
    seeds_path: Path | None = None,
    neo4j=None,
    embedder=None,
) -> RagGroundingResult:
    """RAG 接地主流程（阶段二）。

    Args:
        position_name: candidate 岗位名
        db: PostgreSQL 会话（权威库检索）
        llm: LLMProviderChain（可选，用于定义草案 LLM 生成）
        seeds_path: 种子 yaml 路径（测试可注入）
        neo4j: Neo4j 驱动（缺省用全局 driver；不可用自动降级 ILIKE 关键词路）
        embedder: SkillEmbedder（缺省用全局单例；不可用自动降级关键词路）

    Returns:
        RagGroundingResult：命中状态 + 定义草案
    """
    seeds = _load_seeds(seeds_path or _SEEDS_PATH)
    seed = match_seed(position_name, seeds)

    # 缺省接入真实双路资源；驱动/模型不可用时由 search_authoritative 内部降级
    if neo4j is None:
        from app.core.database import neo4j_driver

        neo4j = neo4j_driver
    if embedder is None:
        from app.services.matching.semantic import SkillEmbedder

        # 单例首次获取会同步加载 SBERT 模型（可达分钟级），放线程池
        embedder = await asyncio.to_thread(SkillEmbedder.get)

    hits = await search_authoritative(position_name, db, neo4j=neo4j, embedder=embedder)
    occupation = hits[0] if hits else None

    if seed is None and occupation is None:
        return RagGroundingResult()

    definition = await _generate_definition(position_name, seed, occupation, llm)
    return RagGroundingResult(
        matched=True,
        seed_matched=seed is not None,
        rag_matched=occupation is not None,
        matched_name=(occupation or seed or {}).get("name", ""),
        occupation_code=(occupation or {}).get("code", ""),
        definition=definition.text,
        definition_source=definition.source,
        nli_contradicted=definition.nli_contradicted,
    )


def _load_seeds(path: Path) -> list[dict]:
    """加载种子列表 yaml。文件缺失/解析失败返回空列表（接地降级为仅权威库）。"""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    return [s for s in data.get("seeds", []) if isinstance(s, dict) and s.get("name")]
