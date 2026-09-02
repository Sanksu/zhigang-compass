"""图谱路由：全景、技能反向查询、全文检索、先修链、学习课程。"""

import asyncio
import json
import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_optional_user
from app.core.database import (
    async_neo4j_driver,
    async_session_factory,
    get_db,
    neo4j_driver,
    redis_client,
)
from app.core.errors import ERR_INTERNAL, ERR_NOT_FOUND, ERR_VALIDATION
from app.models.business import SkillDescription, SkillEmbedding
from app.models.raw import JDRaw
from app.schemas.common import error, ok
from app.services.graph import repository, visibility
from app.services.graph.portrait_evidence import jd_detail, load_position_jd_rows, portrait_evidence
from app.services.graph.skill_descriptions import SKILL_DESCRIPTIONS
from app.services.graph_algorithms.config import load_graph_algo_config
from app.services.learning_path.courses import load_courses_for_skill
from app.services.learning_path.prerequisites import prerequisite_chain
from app.services.matching.semantic import SemanticUnavailableError, SkillEmbedder

logger = logging.getLogger(__name__)

router = APIRouter()

# 全文检索缓存 TTL（08-18 TTL 风暴治理：60s → 300s）
SEARCH_CACHE_TTL = 300

# 缓存穿透合并（08-15 压测扩容）：TTL 失效瞬间 100 并发同时 miss 打
# Neo4j（to_thread 线程池饱和 → P95 20s 长尾根因）。in-flight 表让同 key
# 并发请求只放行 1 个查库，其余 await 同一 future 读缓存。
_inflight: dict[str, asyncio.Future] = {}

# 节点详情缓存 TTL（设计文档 §11.3.5：position:{id} 5min，skill 同档）
_NODE_CACHE_TTL = 300

# 兼容别名：岗位可见性纯函数已迁至 services/graph/visibility.py（单一事实源），
# graph.py 保留同名绑定——tests/graph/test_graph_visibility.py 直读此处。
_PUBLIC_POSITION_STATUSES = visibility._PUBLIC_POSITION_STATUSES
_can_view_all_positions = visibility._can_view_all_positions
_position_scope = visibility._position_scope
_status_clause = visibility._status_clause


async def _cache_get(key: str):
    """Redis 缓存读取（JSON 反序列化），未命中返回 None。"""
    cached = await redis_client.get(key)
    return json.loads(cached) if cached else None


async def _cache_set(key: str, data, ttl: int = _NODE_CACHE_TTL) -> None:
    """Redis 缓存写入（JSON 序列化）。"""
    await redis_client.set(key, json.dumps(data), ex=ttl)


async def invalidate_graph_caches() -> None:
    """图数据变更后失效全部图谱热路径缓存（view/search/节点详情）。

    管理端岗位编辑与审核/归档状态变更后调用（交互路径即时可见）；
    日常 ETL 聚合与自动流转变更由 TTL（300s）兜底。scan 前缀 graph:*
    覆盖 graph:view/graph:search/graph:position/graph:skill 全部键；
    匹配岗位共享缓存（matching:*）不受影响。
    """
    keys = [key async for key in redis_client.scan_iter(match="graph:*")]
    if keys:
        await redis_client.delete(*keys)


async def _query_skill_positions(skill_id: str, status_filter: str) -> list[dict]:
    """skill_positions 热路径查询（P2：async Neo4j 驱动直查）。"""
    return await repository.query_skill_positions_async(async_neo4j_driver, skill_id, status_filter)


async def _query_fulltext_search(
    q: str, type_: str, status_clause: str, offset: int, size: int,
) -> tuple[list[dict], int]:
    """fulltext_search 热路径查询（P2：async Neo4j 驱动直查）。"""
    return await repository.query_fulltext_search_async(
        async_neo4j_driver, q, type_, status_clause, offset, size)


def _query_position_skills_by_necessity(id: str) -> dict[str, dict]:
    """岗位技能（按 necessity 分组，线程池执行，08-14 审查）。"""
    return repository.query_position_skills_by_necessity(neo4j_driver, id)


def _skill_portrait_desc(sk: dict) -> str:
    """岗位画像技能详述：优先取内置词典的专业解释；未收录回退整合模板。"""
    name = str(sk.get("sname") or "").strip().lower()
    cached = SKILL_DESCRIPTIONS.get(name)
    if cached:
        return cached
    scat = str(sk.get("scat") or "通用")
    scount = int(sk.get("scount") or 1)
    need_word = "必备" if sk.get("necessity", "must") == "must" else "加分"
    return (
        f"属于「{scat}」类目，当前岗位共有 {scount} 个独立 JD 直接要求该技能"
        f"（{need_word}）。掌握该技能可直接提升本岗位的必备/加分匹配分，并按其先修链"
        f"逐步补齐前置技能。"
    )


def _query_prereq_chain(skill_name: str) -> list[str]:
    """图谱先修链（线程池执行，08-14 审查）。"""
    return repository.query_prereq_chain(neo4j_driver, skill_name)


def _query_skill_ids(names: list[str]) -> dict[str, str]:
    """技能名 → 图谱 ID（线程池执行）。"""
    return repository.query_skill_ids(neo4j_driver, names)


def _query_all_skills() -> list[tuple[str, str]]:
    """全技能 (id, name)（线程池执行）。"""
    return repository.query_all_skills(neo4j_driver)


async def _query_graph_counts() -> dict:
    """图谱全量节点/边数（stats.total_*，P2：async Neo4j 驱动直查）。"""
    return await repository.query_graph_counts_async(async_neo4j_driver)


def _query_skill_evidence(skill_id: str) -> list[dict]:
    """技能证据列表（线程池执行，08-14 低优先批次）。"""
    return repository.query_skill_evidence(neo4j_driver, skill_id)


def _query_shortest_path(from_skill: str, to_skill: str, statuses) -> list | None:
    """最短路径查询（线程池执行）。"""
    return repository.query_shortest_path(neo4j_driver, from_skill, to_skill, statuses)


async def _query_view_techstack(limit: int, status_filter: str) -> list:
    """techStack 视图热路径查询（P2：async Neo4j 驱动直查）。"""
    return await repository.query_view_techstack_async(async_neo4j_driver, limit, status_filter)


async def _query_view_main(limit: int, status_filter: str) -> list:
    """positionCenter/level/panorama 视图热路径查询（P2：async Neo4j 驱动直查）。"""
    return await repository.query_view_main_async(async_neo4j_driver, limit, status_filter)


@router.get("/skill/{skill_id}/positions")
async def skill_positions(
    skill_id: str,
    user: Optional[dict] = Depends(get_optional_user),
):
    """技能节点反向查询：返回关联的岗位列表 + necessity + weight + level。

    匿名/guest 仅返回 emerging/stable/declining 岗位（candidate 待审核不外宣）。
    """
    scope = _position_scope(user)
    cache_key = f"graph:skill:{skill_id}:positions:{scope}"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return ok(data=cached)
    status_filter = _status_clause(scope)
    positions = await _query_skill_positions(skill_id, status_filter)
    data = {"skill_id": skill_id, "positions": positions}
    await _cache_set(cache_key, data)
    return ok(data=data)


@router.get("/search")
async def fulltext_search(
    q: str = Query(..., min_length=1, max_length=100),
    type_: str = Query(default="position", alias="type", enum=["position", "skill", "evidence"]),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    user: Optional[dict] = Depends(get_optional_user),
):
    """Neo4j 全文检索（匿名可读，cjk 分词器，设计文档 5.4）。

    position/skill 走全文索引；evidence 走 evidence_search 全文索引
    （M17 新增，索引缺失时降级 CONTAINS）。
    匿名/guest 检索岗位时排除 candidate（待审核不外宣），user/admin 含全量。
    """
    scope = _position_scope(user)
    offset = (page - 1) * size
    # 全文检索缓存（08-15 压测扩容）：搜索词重复度高（真实用户/压测同词命中），
    # 每次打 Neo4j 在 100 并发下排队严重；300s TTL 缓存大幅降 Neo4j 压力。
    # 08-18 TTL 治理补 single-flight：同 q 并发冷查合并为一次（此前无合并，
    # 冷键被 2-5 并发同时打 Neo4j fulltext，压测 P99 尾部主因之一）。
    cache_key = f"graph:search:{scope}:{type_}:{q}:{page}:{size}"
    cached = await redis_client.get(cache_key)
    if cached is not None:
        return ok(data=json.loads(cached))

    inflight = _inflight.get(cache_key)
    if inflight is not None:
        return ok(data=await inflight)

    future = asyncio.get_running_loop().create_future()
    _inflight[cache_key] = future
    try:
        # 匿名/guest 检索岗位时排除 candidate（全文索引 YIELD 的是完整节点，可直接过滤）
        status_clause = (
            "WHERE node.status IN $public_statuses" if scope == "public" and type_ == "position" else ""
        )

        items, total = await _query_fulltext_search(q, type_, status_clause, offset, size)
        data = {"items": items, "total": total, "page": page, "size": size}
        await _cache_set(cache_key, data, ttl=SEARCH_CACHE_TTL)
        future.set_result(data)
        return ok(data=data)
    except BaseException as exc:
        # BaseException（第八轮 P2-6）：请求方被取消时 CancelledError 不走
        # Exception 分支——leader 挂掉则 future 永不 resolve，并发跟随者会
        # await 挂死到超时。对未完成 future 注入异常后原样 raise，跟随者快速失败。
        if not future.done():
            future.set_exception(exc)
        raise
    finally:
        _inflight.pop(cache_key, None)


def _load_skill(skill_id: str) -> dict | None:
    """按 ID 查询技能节点（id + name），不存在返回 None。"""
    return repository.load_skill(neo4j_driver, skill_id)


@router.get("/skill/{skill_id}/prerequisites")
async def skill_prerequisites(skill_id: str):
    """技能先修技能链（AL-M4-03，设计文档 §9.5）。

    先修链优先走图谱 PREREQUISITE_OF 边（skill_relations 字典同步产物），
    图谱未建边时回退人工维护字典 configs/skill_prerequisites.yaml；
    返回拓扑序（先修在前），并富化图谱技能 ID。
    """
    skill = await asyncio.to_thread(_load_skill, skill_id)
    if skill is None:
        return error(ERR_NOT_FOUND, "技能不存在", http_status=404)

    chain = await asyncio.to_thread(_query_prereq_chain, skill["name"])
    if not chain:
        chain = prerequisite_chain(skill["name"])
    id_by_name: dict[str, str] = {}
    if chain:
        id_by_name = await asyncio.to_thread(_query_skill_ids, chain)
    prerequisites = [
        {"skill_id": id_by_name.get(name), "name": name, "depth": i + 1}
        for i, name in enumerate(chain)
    ]
    return ok(
        data={
            "skill_id": skill_id,
            "skill_name": skill["name"],
            "prerequisites": prerequisites,
        }
    )


@router.get("/skill/{skill_id}/courses")
async def skill_courses(skill_id: str):
    """技能学习课程列表（AL-M4-03，设计文档 §4.6）。

    图谱 LEARNABLE_VIA 课程按质量分降序返回（质量分来自 course_raw 评估产物），
    top 3 为学习路径推荐课程。
    """
    skill = await asyncio.to_thread(_load_skill, skill_id)
    if skill is None:
        return error(ERR_NOT_FOUND, "技能不存在", http_status=404)

    courses = await load_courses_for_skill(
        skill_id, skill["name"], top_k=None, semantic=await _course_semantic())
    return ok(
        data={
            "skill_id": skill_id,
            "skill_name": skill["name"],
            "courses": [c.model_dump() for c in courses],
        }
    )


async def _course_semantic() -> object | None:
    """课程门控语义器（08-15 审查 M1：graph API 课程与 learning-path 同门控）。

    语义可用：P1-3 标题门控 + 灰色带质量门控全部生效（脏 LEARNABLE_VIA 边
    不再外泄，口径与 compare 学习路径一致）；模型不可用降级 None——courses
    是增强能力，纯规则链路继续（与 match 诊断链路降级一致，不 503 不空列表）。
    """
    try:
        embedder = SkillEmbedder.get()
        # SBERT 探测推理 CPU 密集，放线程池（第八轮 P2-4：对齐同文件
        # skill_similar 的 to_thread 口径——原同步 embed 阻塞事件循环）
        await asyncio.to_thread(embedder.embed, "__probe__")  # 触发惰性加载，探测模型可用性
        return embedder
    except SemanticUnavailableError:
        return None


def _parse_distributions(position: dict) -> dict:
    """图谱 JSON 字符串属性 → 响应对象（education/experience 分布与薪资档位）。"""
    import json as _json

    out: dict = {}
    for key in ("education_distribution", "experience_distribution", "salary_tiers"):
        raw = position.get(key)
        if isinstance(raw, str):
            try:
                out[key] = _json.loads(raw)
            except (ValueError, TypeError):
                out[key] = None
        else:
            out[key] = raw
    return out


def _load_position(id: str, user: Optional[dict] = None) -> dict | None:
    """按 ID 查询岗位节点基础属性（不含技能边），不存在返回 None。

    user/admin 可见全部岗位；匿名/guest 对 candidate/archived 岗位返回 None
    （视为不存在，避免待审核岗位外泄，见方案一）。
    """
    return repository.load_position(neo4j_driver, id, user)


@router.get("/position/{id}")
async def position_detail(
    id: str,
    user: Optional[dict] = Depends(get_optional_user),
):
    """[M4] 岗位节点详情：基础属性 + REQUIRES 技能聚合（must/nice）。

    匿名/guest 对 candidate/archived 岗位返回 404（不可见），user/admin 全量。
    """
    scope = _position_scope(user)
    cache_key = f"graph:position:{id}:{scope}"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return ok(data=cached)
    position = await asyncio.to_thread(_load_position, id, user)
    if position is None:
        return error(ERR_NOT_FOUND, "岗位不存在", http_status=404)

    skills = await asyncio.to_thread(_query_position_skills_by_necessity, id)

    data = {
        "id": position["id"],
        "name": position.get("name", position["id"]),
        "required_years": position.get("required_years"),
        "required_education": position.get("required_education"),
        # 08-29 聚合新增：salary_range 文本解析的月薪资中位区间（元）；
        # salary_range 原文本保留（未解析成功时仍有展示值）
        "salary_min": position.get("salary_min"),
        "salary_max": position.get("salary_max"),
        "salary_range": position.get("salary_range"),
        "salary_currency": position.get("salary_currency"),
        # 08-29 证据计数展示：多值分布（Neo4j 落图为 JSON 字符串，此处还原对象）
        "evidence_count": position.get("evidence_count"),
        **_parse_distributions(position),
        "last_updated": position.get("last_updated"),
        "status": position.get("status"),
        "must_skills": skills.get("must", []),
        "nice_skills": skills.get("nice", []),
        "soft_skills": position.get("soft_skills") or [],
    }
    await _cache_set(cache_key, data)
    return ok(data=data)


@router.get("/position/{id}/portrait-evidence")
async def position_portrait_evidence(
    id: str,
    dimension: Literal["salary", "experience", "education"] = Query(...),
    label: str = Query(default="", max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: Optional[dict] = Depends(get_optional_user),
):
    """[M5] 岗位画像条目证据 JD 列表：薪资/经验/学历条目 → 支撑 JD 回溯。

    label 缺省返回该维度全部证据 JD；条目口径镜像 build_aggregates
    （SimHash 近似重复/归档/岗位级通胀排除一致），见
    services/graph/portrait_evidence.py。匿名/guest 对 candidate/archived
    岗位 404（同 /position/{id}）。Redis 60s 缓存按岗位+维度+标签隔离。
    """
    scope = _position_scope(user)
    cache_key = f"graph:pevidence:{id}:{scope}:{dimension}:{label}:{limit}"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return ok(data=cached)
    position = await asyncio.to_thread(_load_position, id, user)
    if position is None:
        return error(ERR_NOT_FOUND, "岗位不存在或不可见", http_status=404)
    position_name = position.get("name") or id

    result = await portrait_evidence(db, position_name, dimension, label, limit)
    data = {
        "position_id": id,
        "position_name": position_name,
        "dimension": dimension,
        "label": label,
        "total": len(result["items"]),
        "items": result["items"][:limit],
    }
    await _cache_set(cache_key, data)
    return ok(data=data)


@router.get("/jd/{jd_id}")
async def jd_evidence_detail(jd_id: int):
    """[M5] JD 证据正文详情：画像证据列表点开后的原文与出处链接。

    公开招聘信息（脉脉源入库前已脱敏），匿名可读；正文为 jd_raw.raw_text。
    """
    cache_key = f"graph:jd:{jd_id}"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return ok(data=cached)
    async with async_session_factory() as session:
        row = (await session.execute(
            select(JDRaw).where(JDRaw.id == jd_id)
        )).scalar_one_or_none()
    if row is None:
        return error(ERR_NOT_FOUND, "JD 不存在", http_status=404)
    data = jd_detail(row)
    await _cache_set(cache_key, data)
    return ok(data=data)


@router.get("/position/{id}/jds")
async def position_raw_jds(
    id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: Optional[dict] = Depends(get_optional_user),
):
    """[M5] 岗位原始 JD 列表（含全文）：画像侧栏「原始 JD」下拉数据源。

    复用聚合口径（SimHash 近似重复/归档/岗位级通胀排除一致，见
    portrait_evidence.load_position_jd_rows），返回最新优先的 JD 及其 raw_text，
    供前端下拉选择后展开正文。匿名/guest 对 candidate/archived 岗位 404。
    """
    scope = _position_scope(user)
    cache_key = f"graph:pjds:{id}:{scope}:{offset}:{limit}"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return ok(data=cached)
    position = await asyncio.to_thread(_load_position, id, user)
    if position is None:
        return error(ERR_NOT_FOUND, "岗位不存在或不可见", http_status=404)
    position_name = position.get("name") or id
    rows = await load_position_jd_rows(db, position_name)
    page = rows[offset : offset + limit]
    items = [jd_detail(r) for r in page]
    data = {
        "position_id": id,
        "position_name": position_name,
        "total": len(rows),
        "offset": offset,
        "limit": limit,
        "items": items,
    }
    await _cache_set(cache_key, data)
    return ok(data=data)


@router.get("/skill/{skill_id}/evidence")
async def skill_evidence(skill_id: str):
    """[M4] 技能证据列表：Skill-EVIDENCED_BY->Evidence 原始 JD。"""
    cache_key = f"graph:skill:{skill_id}:evidence"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return ok(data=cached)
    skill = await asyncio.to_thread(_load_skill, skill_id)
    if skill is None:
        return error(ERR_NOT_FOUND, "技能不存在", http_status=404)

    evidence = await asyncio.to_thread(_query_skill_evidence, skill_id)

    data = {
        "skill_id": skill_id,
        "skill_name": skill["name"],
        "evidence": evidence,
        "evidence_count": len(evidence),
    }
    await _cache_set(cache_key, data)
    return ok(data=data)


@router.get("/skill/similar")
async def skill_similar(
    skill_id: str = Query(..., max_length=100),
    top_k: int = Query(default=10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """[M4] 相似技能检索（语义相似度，设计文档 5.3 pgvector 演进落地）。

    主路径：pgvector skill_embeddings 余弦距离 Top-K（§11.4.3，IVFFLAT）。
    未回填或查询失败（表缺失/维度不匹配等）时回退内存 SBERT 全量扫描，口径一致。
    阈值 0.5，过低不返回；SBERT 不可用时返回 503（语义能力缺失，不降级为猜）。
    """
    skill = await asyncio.to_thread(_load_skill, skill_id)
    if skill is None:
        return error(ERR_NOT_FOUND, "技能不存在", http_status=404)

    cache_key = f"graph:skill:similar:{skill_id}:{top_k}"
    cached = await _cache_get(cache_key)
    if cached is not None:
        return ok(data=cached)

    embedder = SkillEmbedder.get()

    # pgvector 主路径：skill_embeddings 已回填则余弦距离 Top-K。
    # 查询异常（表缺失/维度不匹配）降级回退内存扫描；语义模型不可用则 503。
    try:
        target_vec_row = await db.get(SkillEmbedding, skill_id)
        if target_vec_row is not None:
            # SBERT 推理 CPU 密集，放线程池避免阻塞事件循环
            qvec = await asyncio.to_thread(embedder.embed, skill["name"])
            rows = (
                await db.scalars(
                    select(SkillEmbedding)
                    .where(SkillEmbedding.id != skill_id)
                    .order_by(SkillEmbedding.embedding.cosine_distance(qvec))
                    .limit(200)  # 多取后按阈值过滤，保证 Top-K 质量
                )
            ).all()
            similar = [
                (r.id, r.payload.get("name", r.id), 1.0 - float(r.embedding.cosine_distance(qvec)))
                for r in rows
            ]
            similar = sorted((s for s in similar if s[2] >= 0.5), key=lambda x: x[2], reverse=True)[:top_k]
            data = {
                "skill_id": skill_id,
                "skill_name": skill["name"],
                "similar": [
                    {"skill_id": sid, "skill_name": name, "similarity": round(score, 4)}
                    for sid, name, score in similar
                ],
            }
            await _cache_set(cache_key, data)
            return ok(data=data)
    except SemanticUnavailableError:
        return error(ERR_INTERNAL, "语义模型不可用，无法计算相似技能", http_status=503)
    except Exception as exc:
        # skill_embeddings 表缺失 / 向量维度不匹配等 → 降级回退内存扫描
        logger.warning("pgvector 技能相似查询降级回退内存扫描: %s", exc)

    # 回退路径：skill_embeddings 未回填（表空/缺该技能），内存 SBERT 全量扫描
    all_skills = await asyncio.to_thread(_query_all_skills)
    if not all_skills:
        data = {"skill_id": skill_id, "skill_name": skill["name"], "similar": []}
        await _cache_set(cache_key, data)
        return ok(data=data)

    def _sbert_scan() -> list:
        # 全量两两相似度 SBERT 推理 CPU 密集，放线程池避免阻塞事件循环
        return [
            (sid, name, embedder.similarity(skill["name"], name))
            for sid, name in all_skills
            if sid != skill_id
        ]

    try:
        scores = await asyncio.to_thread(_sbert_scan)
    except SemanticUnavailableError:
        return error(ERR_INTERNAL, "语义模型不可用，无法计算相似技能", http_status=503)

    similar = sorted(
        (s for s in scores if s[2] >= 0.5),
        key=lambda x: x[2],
        reverse=True,
    )[:top_k]
    data = {
        "skill_id": skill_id,
        "skill_name": skill["name"],
        "similar": [
            {"skill_id": sid, "skill_name": name, "similarity": round(score, 4)}
            for sid, name, score in similar
        ],
    }
    await _cache_set(cache_key, data)
    return ok(data=data)


# 图算法默认参数（import 时从 configs/graph_algo.yaml 读取——Optuna 最优 γ/min_weight
# 接入 API 运行时路径，与 sync_communities 索引同口径；修改配置后重启生效）
_GRAPH_ALGO_DEFAULTS = load_graph_algo_config()


@router.get("/algorithms/pagerank")
async def graph_pagerank(
    top_n: int = Query(default=20, ge=1, le=100),
    min_weight: float = Query(default=_GRAPH_ALGO_DEFAULTS["min_weight"], ge=1.0),
):
    """PageRank 技能重要性 Top-N（设计文档 7.1 图算法应用）。

    技能网络 = 岗位共现（两技能被同一岗位 REQUIRES 即连边），纯 Python
    幂迭代（Neo4j 社区版无 GDS 插件）。min_weight 默认取 configs/graph_algo.yaml
    （调优值 2.5021，与 skill-clusters 端点取数口径一致）。30s Redis TTL 缓存。
    """
    from app.services.graph_algorithms.network import load_skill_cooccurrence
    from app.services.graph_algorithms.pagerank import pagerank

    cache_key = f"graph:algo:pagerank:{top_n}:{min_weight}"
    cached = await redis_client.get(cache_key)
    if cached is not None:
        return ok(data=json.loads(cached))

    def _compute():
        # 同步 Neo4j 会话 + 幂迭代为 CPU/IO 密集，放线程池避免阻塞事件循环
        with neo4j_driver.session() as session:
            graph, name_map = load_skill_cooccurrence(session, min_weight=min_weight)
        scores = pagerank(graph)
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
        return name_map, ranked

    name_map, ranked = await asyncio.to_thread(_compute)
    skills = [
        {"id": sid, "name": name_map.get(sid, sid), "score": round(score, 6)}
        for sid, score in ranked
    ]
    data = {"skills": skills, "top_n": len(skills)}
    await redis_client.set(cache_key, json.dumps(data), ex=30)
    return ok(data=data)


@router.get("/algorithms/skill-clusters")
async def graph_skill_clusters(
    min_size: int = Query(default=2, ge=1, le=100),
    resolution: float = Query(default=_GRAPH_ALGO_DEFAULTS["resolution"], ge=0.1, le=5.0),
    level: Optional[int] = Query(default=None, ge=0, le=31),
):
    """Louvain/Leiden 技能簇（设计文档 7.1 图算法应用，技术栈视图支撑）。

    同一簇内技能常共现于同一批岗位（如大数据栈 / AI 栈）。聚类算法由
    configs/graph_algo.yaml 的 algorithm 字段决定（louvain 默认，阶段二
    Leiden 条件替换：同签名 leiden()，seed=0 确定性；依赖缺失自动回退
    louvain 并告警）。min_size 过滤过小簇；resolution 为分辨率参数 γ
    （图算法优化方案阶段一：>1 细簇 / <1 粗簇 / 1.0 等价标准 Louvain，
    默认值取 configs/graph_algo.yaml）。

    阶段三层次化提取：level 指定 dendrogram 层级（0 = 最细，逐层变粗，
    默认 None = 最优层，与 louvain() 输出一致）；响应附 levels 元数据
    （level/cluster_count/modularity，供前端层级导航；Leiden 算法不支持
    层级，level 参数忽略且 levels 为 null）。

    图算法优化方案 §4：输出经规则优先后处理（孤立簇剔除/过小簇合并/
    规则标签）+ LLM 兜底（仅 needs_llm 簇调用，失败降级规则标签），
    响应附 needs_llm/triggers/llm 字段。30s Redis TTL 缓存（键含
    algorithm + resolution + level，防新旧参数/算法/层级串缓存）。
    """
    from app.services.extraction.dictionary import skill_category
    from app.services.graph_algorithms.cluster_llm import ClusterLLMClassifier
    from app.services.graph_algorithms.network import load_skill_cooccurrence
    from app.services.graph_algorithms.postprocess import ClusterPostProcessor

    algorithm = load_graph_algo_config()["algorithm"]
    cache_key = f"graph:algo:clusters:{algorithm}:{min_size}:{resolution}:{level}"
    cached = await redis_client.get(cache_key)
    if cached is not None:
        return ok(data=json.loads(cached))

    def _run_clustering(graph):
        """按配置选择聚类算法：leiden 优先（依赖缺失回退 louvain 并告警）。"""
        if algorithm == "leiden":
            try:
                from app.services.graph_algorithms.leiden import leiden

                return leiden(graph, resolution=resolution), None
            except ImportError:
                logger.warning(
                    "leiden 依赖（igraph/leidenalg）不可用，回退 louvain（algorithm=%s）", algorithm
                )
        from app.services.graph_algorithms.louvain import louvain_hierarchical

        hier = louvain_hierarchical(graph, resolution=resolution)
        if level is None:
            return hier["membership"], hier
        # 指定层级：不存在（越界）时回退最粗层
        by_level = {lv["level"]: lv["membership"] for lv in hier["levels"]}
        membership = by_level.get(level, hier["levels"][-1]["membership"])
        return membership, hier

    def _compute():
        # 同步 Neo4j 会话 + 聚类 + 后处理 + LLM 兜底为 CPU/IO 密集，
        # 放线程池避免阻塞事件循环。
        # min_weight=2.0（默认）：P0 改造后权重=必要性组合因子×共现数，
        # 过滤 must-nice 低频与 nice-nice 弱边，聚类簇内同质性最佳
        with neo4j_driver.session() as session:
            graph, name_map = load_skill_cooccurrence(session, min_weight=_GRAPH_ALGO_DEFAULTS["min_weight"])
        clusters, hier = _run_clustering(graph)

        # 规则优先后处理 + LLM 兜底触发标记（图算法优化方案 §4.1-4.2）
        categories = {sid: skill_category(name) for sid, name in name_map.items()}
        processed = ClusterPostProcessor().process(clusters, graph, name_map, categories)

        # LLM 兜底（§4.3-4.4）：仅对 needs_llm 且非孤立的簇调用，失败降级规则标签
        # （ClusterLLMClassifier.classify 内部已捕获 LLM 异常，不阻塞 API）
        classifier = ClusterLLMClassifier()
        for c in processed["clusters"]:
            if c["needs_llm"] and not c["orphan"]:
                skills = [name_map.get(sid, sid) for sid in c["skills"]]
                decision = classifier.classify(skills, c["triggers"], c["label"])
                c["llm"] = {
                    "coherent": decision.coherent,
                    "cluster_name": decision.cluster_name,
                    "rationale": decision.rationale,
                    "splits": decision.splits,
                }

        items = []
        for c in processed["clusters"]:
            if c["orphan"]:
                continue
            if len(c["skills"]) < min_size:
                continue
            items.append({
                "id": c["cluster_id"],
                "size": len(c["skills"]),
                "label": c["label"],
                "needs_llm": c["needs_llm"],
                "triggers": c["triggers"],
                "llm": c.get("llm"),
                "skills": [{"id": sid, "name": name_map.get(sid, sid)} for sid in c["skills"]],
            })
        # 层级元数据：每层经后处理（无 LLM）+ min_size 过滤后的实际簇数，
        # 与对应 level 请求的结果同口径（细层单点簇会被过小簇合并，须如实反映）；
        # modularity 统一用标准 Q（γ=1.0），与评估报告/验收口径一致
        level_counts = None
        if hier is not None:
            from app.services.graph_algorithms.louvain import modularity

            level_counts = []
            for lv in hier["levels"]:
                lv_processed = ClusterPostProcessor().process(lv["membership"], graph, name_map, categories)
                n = sum(
                    1 for c in lv_processed["clusters"]
                    if not c["orphan"] and len(c["skills"]) >= min_size
                )
                level_counts.append({
                    "level": lv["level"],
                    "cluster_count": n,
                    "modularity": round(modularity(graph, lv["membership"], 1.0), 6),
                })
        return items, level_counts

    items, level_counts = await asyncio.to_thread(_compute)
    data = {"clusters": items, "cluster_count": len(items)}
    # 阶段三层级元数据（Leiden 不支持层级时为 null，前端隐藏层级导航）
    data["levels"] = level_counts
    await redis_client.set(cache_key, json.dumps(data), ex=30)
    return ok(data=data)


@router.get("/algorithms/community-tree")
async def graph_community_tree():
    """社区层级树（图算法优化方案阶段三：层次化提取，dendrogram 可视化）。

    读取 scripts/sync_communities.py 写入的 Neo4j Community 节点：
    - 节点：`(:Community {id: comm_{level}_{cluster}, name, level, modularity, cluster_count})`
    - 边：`(:Skill)-[:BELONGS_TO_COMMUNITY]->(:Community)` + `(:Community)-[:NESTED_IN]->(:Community)`

    响应为树结构（顶层 = 最高层社区，children 按 NESTED_IN 展开），
    供 ECharts tree 系列 dendrogram 渲染。未同步（无 Community 节点）时
    返回空树（前端提示先运行 scripts/sync_communities.py）。
    30s Redis TTL 缓存。
    """
    cache_key = "graph:algo:community-tree"
    cached = await redis_client.get(cache_key)
    if cached is not None:
        return ok(data=json.loads(cached))

    def _compute():
        with neo4j_driver.session() as session:
            rows = session.run(
                """
                MATCH (s:Skill)-[r:BELONGS_TO_COMMUNITY]->(c:Community)
                RETURN c.id AS cid, c.level AS level, c.name AS name,
                       c.cluster_count AS cluster_count, c.modularity AS modularity,
                       collect(s.name)[..5] AS top_skills
                """
            )
            nodes: dict[str, dict] = {}
            for rec in rows:
                cid = rec["cid"]
                nodes[cid] = {
                    "id": cid,
                    "name": rec.get("name") or cid,
                    "level": rec["level"],
                    "cluster_count": rec["cluster_count"] or 0,
                    "modularity": rec["modularity"] or 0.0,
                    "top_skills": rec.get("top_skills") or [],
                    "children": [],
                }
            parent_rows = session.run(
                "MATCH (c1:Community)-[:NESTED_IN]->(c2:Community) RETURN c1.id AS child, c2.id AS parent"
            )
            children_of: dict[str, list[str]] = {}
            for rec in parent_rows:
                children_of.setdefault(rec["parent"], []).append(rec["child"])
        # 组装树：children 按 NESTED_IN 展开；根 = 无父节点（最高层社区）
        for cid, child_ids in children_of.items():
            if cid in nodes:
                nodes[cid]["children"] = [nodes[c] for c in child_ids if c in nodes]
        child_set = {c for children in children_of.values() for c in children}
        roots = [n for n in nodes.values() if n["id"] not in child_set]
        levels = sorted({n["level"] for n in nodes.values()})
        return {"tree": roots, "levels": levels}

    data = await asyncio.to_thread(_compute)
    await redis_client.set(cache_key, json.dumps(data), ex=30)
    return ok(data=data)


@router.get("/algorithms/shortest-path")
async def graph_shortest_path(
    from_skill: str = Query(..., alias="from"),
    to_skill: str = Query(..., alias="to"),
    user: Optional[dict] = Depends(get_optional_user),
):
    """技能最短路径（设计文档 7.1 图算法应用，学习路径先修排序）。

    shortestPath((:Skill)-[*..6]-(:Skill))，路径可能经过 Position 节点
    （岗位共现边），节点序列按 type 区分。不存在可达路径返回 404。
    匿名/guest 路径经过的 Position 节点仅限公开态（candidate 不外宣）。
    """

    scope = _position_scope(user)
    statuses = list(_PUBLIC_POSITION_STATUSES) if scope == "public" else None
    path = await asyncio.to_thread(
        _query_shortest_path, from_skill, to_skill, statuses)
    if path is None:
        return error(ERR_NOT_FOUND, "两技能间不存在 ≤6 跳的可达路径", http_status=404)
    return ok(data={"from": from_skill, "to": to_skill, "path": path})


async def _load_desc_overrides(db: AsyncSession) -> dict[str, str]:
    """技能解释 DB 覆盖表 → {skill_name: description}（低基数，请求级读取）。"""
    rows = await db.execute(select(SkillDescription))
    return {r.skill_name: r.description for r in rows.scalars().all()}


def _skill_portrait_desc(sk: dict, overrides: Optional[dict] = None) -> str:
    """岗位画像技能详述：DB 覆盖 > 内置词典 > 整合模板。"""
    name = str(sk.get("sname") or "").strip().lower()
    if overrides:
        ov = overrides.get(name)
        if ov:
            return ov
    cached = SKILL_DESCRIPTIONS.get(name)
    if cached:
        return cached
    scat = str(sk.get("scat") or "通用")
    scount = int(sk.get("scount") or 1)
    need_word = "必备" if sk.get("necessity", "must") == "must" else "加分"
    return (
        f"属于「{scat}」类目，当前岗位共有 {scount} 个独立 JD 直接要求该技能"
        f"（{need_word}）。掌握该技能可直接提升本岗位的必备/加分匹配分，并按其先修链"
        f"逐步补齐前置技能。"
    )


@router.get("/view/{view_type}")
async def graph_view(
    view_type: Literal["panorama", "techStack", "positionCenter", "positionPortrait"],
    position: Optional[str] = Query(
        default=None, max_length=100,
        description="岗位 id/name（positionPortrait 视图必填）"),
    limit: int = Query(default=100, ge=1, le=600),
    user: Optional[dict] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """[M4] 视图切换（匿名可读，后端过滤，同构于全景图）。

    三种页面视图统一返回 {view_type, nodes, edges, stats}：
    - panorama: 域聚合下钻的岗位中心展开（岗位→技能）
    - techStack: 技能为中心，边反向为技能→岗位，节点按技能频次排序
    - positionCenter: 与 panorama 同查询的岗位中心展开——无独立页签，
      作为岗位画像（positionPortrait）下拉岗位选项的数据源保留
    匿名/guest 仅返回 emerging/stable/declining 岗位（candidate 待审核不外宣）。
    """
    scope = _position_scope(user)
    # positionPortrait 缓存按岗位隔离（岗位切换频繁，TTL 内仍命中同岗位）
    cache_key = f"graph:view:{view_type}:{limit}:{scope}" + (
        f":{position}" if view_type == "positionPortrait" else ""
    )
    cached = await redis_client.get(cache_key)
    if cached is not None:
        return ok(data=json.loads(cached))

    status_filter = _status_clause(scope)
    # 技能解释 DB 覆盖（仅 positionPortrait 需要；空 dict 时走词典/模板）
    desc_overrides: dict[str, str] = {}

    if view_type == "positionPortrait":
        if not position:
            return error(ERR_VALIDATION, "positionPortrait 视图必须指定 position 参数")
        desc_overrides = await _load_desc_overrides(db)
        rows = await repository.query_view_position_portrait_async(
            async_neo4j_driver, position, limit, status_filter)
        if not rows:
            return error(ERR_NOT_FOUND, "岗位不存在或不可见", http_status=404)
        record = rows[0]
        # salary_tiers / 分布属性在图上为 JSON 字符串（Neo4j 不收 Map），先还原
        p = {**{k: record["p"].get(k) for k in record["p"].keys()},
             **_parse_distributions(record["p"])}
        pid = p.get("id", position)
        nodes: list[dict] = [{
            "id": pid,
            "name": p.get("name", position),
            "type": "position",
            "status": p.get("status") or "active",
            "evidence_count": p.get("evidence_count"),
            "value": p.get("freq", 0),
        }]
        edges: list[dict] = []

        # 层级画像（08-29 拍板）：岗位 → 大类节点（技能/软技能/薪资/经验/学历）
        # → 各自小节点。大类 type=attr，无数据的维度不生成。
        def _add_category(label: str) -> str:
            cat_id = f"attr_{pid}_{label}"
            nodes.append({
                "id": cat_id, "name": label, "type": "attr",
                "skill_category": label, "portrait_category": True,
            })
            edges.append({"source": pid, "target": cat_id, "weight": 1.0})
            return cat_id

        def _add_child(cat_id: str, child_id: str, name: str, extra: dict | None = None):
            node = {"id": child_id, "name": name, "type": "attr", "skill_category": "画像条目"}
            if extra:
                node.update(extra)
            nodes.append(node)
            edges.append({"source": cat_id, "target": child_id, "weight": 1.0})

        # 技能大类 → 具体技能 Top-15（type=skill 保留类目着色，weight 降序）
        skill_children = [sk for sk in (record.get("skills") or []) if sk.get("sid")]
        if skill_children:
            cat_id = _add_category("技能")
            for sk in skill_children[:15]:
                nodes.append({
                    "id": sk["sid"],
                    "name": sk.get("sname") or sk["sid"],
                    "type": "skill",
                    "skill_category": sk.get("scat"),
                    "value": sk.get("weight", 0),
                    # 岗位画像独有：该技能被当前岗位几个独立 JD 直接要求（REQUIRES.source_count）
                    "jd_source_count": sk.get("scount", 1),
                    # 技能解释：DB 覆盖 > 内置词典 > 整合模板（编辑/LLM 补齐走 DB）
                    "description": _skill_portrait_desc(sk, desc_overrides),
                })
                edges.append({
                    "source": cat_id,
                    "target": sk["sid"],
                    "weight": sk.get("weight", 0.0),
                    "necessity": sk.get("necessity", "must"),
                    "level": sk.get("level", "中级"),
                })
        # 软技能大类 → soft_skills 列表逐项（type=skill + 软技能类目 → 前端粉色；
        # 写回顺序即 JD 命中降序，无计数只展示名字）
        soft_skills = p.get("soft_skills") or []
        if soft_skills:
            cat_id = _add_category("软技能")
            for i, name in enumerate(soft_skills[:15]):
                _add_child(cat_id, f"soft_{pid}_{i}", name,
                           {"type": "skill", "skill_category": "软技能"})
        # 薪资大类 → 档位 Top-5（'1-1.3万 ×9'）；无档位时单条兜底
        # （salary_min/max 拼接或 salary_range 原文）
        salary_tiers = p.get("salary_tiers") or []
        salary_fallback = p.get("salary_range") or (
            f"{p.get('salary_min')}-{p.get('salary_max')}"
            f"{'元' if p.get('salary_currency') == 'CNY' else ' USD'}"
            if p.get("salary_min") is not None else None
        )
        if salary_tiers or salary_fallback:
            cat_id = _add_category("薪资")
            if salary_tiers:
                for i, tier in enumerate(salary_tiers[:5]):
                    _add_child(cat_id, f"sal_{pid}_{i}",
                               f'{tier.get("text", "?")} ×{tier.get("count", 0)}')
            else:
                _add_child(cat_id, f"sal_{pid}_fallback", salary_fallback)
        # 经验大类 → 分布 Top-5（'3年以上 ×122'）
        exp_dist = p.get("experience_distribution") or {}
        if exp_dist:
            cat_id = _add_category("经验")
            for i, (label, cnt) in enumerate(exp_dist.items()):
                if i >= 5:
                    break
                _add_child(cat_id, f"exp_{pid}_{i}", f"{label} ×{cnt}")
        # 学历大类 → 分布 Top-5（'本科 ×245'）
        edu_dist = p.get("education_distribution") or {}
        if edu_dist:
            cat_id = _add_category("学历")
            for i, (label, cnt) in enumerate(edu_dist.items()):
                if i >= 5:
                    break
                _add_child(cat_id, f"edu_{pid}_{i}", f"{label} ×{cnt}")
        data = {
            "view_type": view_type,
            "nodes": nodes,
            "edges": edges,
            "stats": {"nodes": len(nodes), "edges": len(edges),
                      **await _query_graph_counts()},
        }
    elif view_type == "techStack":
        rows = await _query_view_techstack(limit, status_filter)
        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        for record in rows:
            s_id, p_id = record["sid"], record["pid"]
            nodes.setdefault(s_id, {
                "id": s_id,
                "name": record.get("sname", s_id),
                "type": "skill",
                "skill_category": record.get("s_category"),
            })
            nodes.setdefault(p_id, {
                "id": p_id,
                "name": record.get("pname", p_id),
                "type": "position",
                "status": record.get("pstatus") or "active",
            })
            edges.append({
                "source": s_id,
                "target": p_id,
                "weight": record["r"].get("weight", 0.0),
                "necessity": record["r"].get("necessity", "must"),
                "level": record["r"].get("level", "中级"),
            })
        data = {
            "view_type": view_type,
            "nodes": list(nodes.values()),
            "edges": edges,
            "stats": {"nodes": len(nodes), "edges": len(edges),
                      **await _query_graph_counts()},
        }
    else:
        rows = await _query_view_main(limit, status_filter)
        nodes = {}
        edges = []
        for record in rows:
            p, s, r = record["p"], record["s"], record["r"]
            p_id, s_id = p.get("id", ""), s.get("id", "")
            nodes.setdefault(p_id, {
                "id": p_id,
                "name": p.get("name", p_id),
                "type": "position",
                "status": p.get("status", "active"),
                "domain_id": p.get("domain_id"),
                "domain_name": p.get("domain_name"),
            })
            nodes.setdefault(s_id, {
                "id": s_id,
                "name": s.get("name", s_id),
                "type": "skill",
                "skill_category": s.get("category"),
            })
            edges.append({
                "source": p_id,
                "target": s_id,
                "weight": r.get("weight", 0.0),
                "necessity": r.get("necessity", "must"),
                "level": r.get("level", "中级"),
            })
        data = {
            "view_type": view_type,
            "nodes": list(nodes.values()),
            "edges": edges,
            "stats": {"nodes": len(nodes), "edges": len(edges),
                      **await _query_graph_counts()},
        }

    await redis_client.set(cache_key, json.dumps(data), ex=30)
    return ok(data=data)
