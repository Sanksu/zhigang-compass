"""学习课程加载（AL-M4-03，设计文档 §4.6）。

链路：图谱 (Skill)-[:LEARNABLE_VIA]->(Course) → 关联 PostgreSQL
course_raw.snapshot["quality"]（DA-M4-01 课程质量评估产物）→ 按质量分降序取 Top-3。
未评估课程（质量分缺失）排在有分课程之后，属于合法状态不阻断。

技能匹配注意：课程入图时按精确技能名建 LEARNABLE_VIA（kg_service），而岗位/简历
技能多为中文名（如"AI"），与课程技能（ESCO 英文标准名）不同名 → 精确查询会落空。
故无精确命中时启用语义相似度 fallback（复用匹配引擎 SkillEmbedder），按
sim_threshold 找图谱中有课程的相近技能，返回其课程。
"""

import asyncio
import re
import threading
import time

from sqlalchemy import select, tuple_

from app.core.database import async_session_factory, neo4j_driver
from app.models.raw import CourseRaw
from app.services.learning_path.schemas import CourseRecommendation

# 学习路径按质量分取 Top-3（设计文档 §4.6）
_TOP_COURSES = 3

# 有课程技能名缓存（TTL 5min，避免每次 fallback 全图扫描）
_CACHE_TTL = 300
_course_skills_cache: dict = {"ts": 0.0, "names": []}
_cache_lock = threading.Lock()

# 课程推荐语义阈值（宽松于人岗匹配 sim_threshold=0.831）：课程是"建议学该方向"，
# 语义相关即可推荐；匹配则是"是否同一技能"须严格。0.7 可覆盖 "Conversational AI"→"Generative AI"(0.707)
_COURSE_MATCH_THRESHOLD = 0.7

# P1-3 课程名语义门控阈值：课程名与技能名的相关性下限（08-13 实证）。
# 误配课程相似度 0.01-0.25（Genomic Data Science/期末冲刺/Node.js 等，图谱脏边），
# 合理课程 0.6+（Python for Everybody 0.796、机器学习↔Machine Learning 0.939）；
# 0.5 过滤全部实证误配、保留合理课程（统计分析→DS Fundamentals 0.304 等偏案例同滤，
# 宁可无课不可误导）。注意同语言短词虚高（语音合成↔KK音标 0.665）为已知残余。
_COURSE_TITLE_SIM_THRESHOLD = 0.5

# 时长单位 → 小时换算（周/月按每周 40h / 每月 160h 折算）
_UNIT_HOURS = {
    "小时": 1.0, "hour": 1.0, "hours": 1.0, "h": 1.0,
    "天": 8.0, "day": 8.0, "days": 8.0,
    "周": 40.0, "week": 40.0, "weeks": 40.0,
    "月": 160.0, "month": 160.0, "months": 160.0,
    "年": 1920.0, "year": 1920.0, "years": 1920.0,
}
# 单位关键词按长度降序（"hours" 先于 "hour"，避免长单位被短单位前缀截断）
_UNIT_PREFIXES = sorted(_UNIT_HOURS, key=len, reverse=True)


def parse_duration_hours(duration: str | None) -> float | None:
    """解析课程时长字符串为小时；无法解析返回 None。

    支持 "X 周/月/天/小时" 与 "X weeks/months/days/hours" 等常见格式；
    单位后允许跟随"左右/上下"等语气词（如 "约 4 周左右"）。
    """
    if not duration:
        return None
    text = str(duration).strip().lower()
    # 数字后可能带"个"（如"2 个月"），随后是单位（中英文）
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:个)?([a-z\u4e00-\u9fff]+)", text)
    if not m:
        return None
    try:
        num = float(m.group(1))
    except ValueError:
        return None
    unit = m.group(2)
    for prefix in _UNIT_PREFIXES:
        if unit.startswith(prefix):
            return round(num * _UNIT_HOURS[prefix], 1)
    return None


async def _load_quality_map(keys: list[tuple[str, str]]) -> dict[tuple[str, str], dict]:
    """按 (source, source_id) 批量关联课程质量分。"""
    if not keys:
        return {}
    async with async_session_factory() as session:
        rows = (
            await session.scalars(
                select(CourseRaw).where(tuple_(CourseRaw.source, CourseRaw.source_id).in_(keys))
            )
        ).all()
    quality: dict[tuple[str, str], dict] = {}
    for row in rows:
        q = (row.snapshot or {}).get("quality")
        if q and q.get("quality_score") is not None:
            quality[(row.source, row.source_id)] = q
    return quality


def _skills_with_courses() -> list[str]:
    """图谱中有课程的全部技能名（TTL 缓存，供语义 fallback 匹配）。"""
    now = time.time()
    with _cache_lock:
        if now - _course_skills_cache["ts"] <= _CACHE_TTL and _course_skills_cache["names"]:
            return _course_skills_cache["names"]
    with neo4j_driver.session() as session:
        recs = session.run(
            "MATCH (s:Skill)-[:LEARNABLE_VIA]->(:Course) RETURN DISTINCT s.name AS name"
        )
        names = [r["name"] for r in recs]
    with _cache_lock:
        _course_skills_cache["ts"] = now
        _course_skills_cache["names"] = names
    return names


def _semantic_match_skill(
    skill_name: str, semantic, sim_threshold: float
) -> str | None:
    """用语义相似度找图谱中有课程的相近技能名（> threshold 才接受）。"""
    names = _skills_with_courses()
    if not names or not skill_name:
        return None
    semantic.warm(names)
    best_name, best_sim = None, 0.0
    for n in names:
        try:
            sim = semantic.similarity(skill_name, n)
        except Exception:
            continue
        if sim > best_sim:
            best_sim, best_name = sim, n
    return best_name if best_sim > sim_threshold else None


def _filter_by_title_similarity(
    rows: list[dict], skill_name: str, semantic, title_threshold: float
) -> list[dict]:
    """课程名校验（P1-3）：课程名与技能名语义相关才保留。

    背景：图谱存在脏 LEARNABLE_VIA 边（Unix Shell→Genomic Data Science、
    Servers→Node.js 等）与脏技能节点（发音纠正打卡/期末突击），fallback 或
    精确边都可能返回与技能无关的课程。课程名↔技能名相似度实证（08-13）：
    误配 0.01-0.25、合理 0.6+，阈值 0.5 可过滤误配、保留合理。语义模型不可用
    时不过滤（保持纯规则链路）。
    """
    if not rows or semantic is None:
        return rows
    kept = []
    for r in rows:
        title = r.get("name") or r.get("id") or ""
        if not title:
            continue
        try:
            sim = semantic.similarity(skill_name, title)
        except Exception:
            continue
        if sim >= title_threshold:
            kept.append(r)
    return kept


def _query_courses_sync(skill_id: str, skill_name: str) -> list[dict]:
    """图谱精确查询技能课程（skill_id 优先，name 兜底）。同步 Neo4j，由线程池调用。"""
    with neo4j_driver.session() as session:
        return [
            dict(rec)
            for rec in session.run(
                """
                MATCH (s:Skill)-[:LEARNABLE_VIA]->(c:Course)
                WHERE s.id = $skill_id OR s.name = $skill_name
                RETURN c.id AS id, c.name AS name, c.source AS source,
                       c.source_id AS source_id, c.platform AS platform,
                       c.duration AS duration, c.source_url AS source_url
                """,
                skill_id=skill_id,
                skill_name=skill_name,
            )
        ]


async def _query_courses(skill_id: str, skill_name: str) -> list[dict]:
    """图谱精确查询技能课程（Neo4j 同步调用放线程池，避免阻塞事件循环）。"""
    return await asyncio.to_thread(_query_courses_sync, skill_id, skill_name)


async def load_courses_for_skill(
    skill_id: str,
    skill_name: str,
    top_k: int | None = _TOP_COURSES,
    semantic=None,
    sim_threshold: float | None = None,
) -> list[CourseRecommendation]:
    """查询技能可学习课程，按质量分降序返回（top_k=None 返回全量）。

    Args:
        skill_id: 图谱技能 ID（可空串，按 name 匹配兜底）
        skill_name: 技能名
        top_k: 返回条数，None 为全量；缺省 Top-3（设计文档 §4.6）
        semantic: Sentence-BERT 相似度器。精确命中为空时，用它按阈值匹配图谱中
            有课程的相近技能（岗位中文技能 vs 课程英文标准名场景）
        sim_threshold: 课程语义命中阈值，None 用课程专用阈值 _COURSE_MATCH_THRESHOLD
    """
    rows = await _query_courses(skill_id, skill_name)
    if not rows and semantic is not None:
        threshold = _COURSE_MATCH_THRESHOLD if sim_threshold is None else sim_threshold
        # 语义 fallback 含同步 Neo4j 全图扫描与 SBERT 计算，放线程池避免阻塞事件循环
        matched = await asyncio.to_thread(_semantic_match_skill, skill_name, semantic, threshold)
        if matched:
            rows = await _query_courses("", matched)
    if not rows:
        return []

    # P1-3：课程名语义门控（精确边与 fallback 统一校验，防图谱脏边/脏技能误配）
    if semantic is not None:
        rows = await asyncio.to_thread(
            _filter_by_title_similarity, rows, skill_name, semantic,
            _COURSE_TITLE_SIM_THRESHOLD,
        )
        if not rows:
            return []

    quality = await _load_quality_map([(r["source"], r["source_id"]) for r in rows])
    items = [
        CourseRecommendation(
            course_id=r["id"],
            title=r.get("name") or r["id"],
            platform=r.get("platform") or "",
            quality_score=quality[(r["source"], r["source_id"])]["quality_score"]
            if (r["source"], r["source_id"]) in quality
            else None,
            recommended=bool(quality[(r["source"], r["source_id"])].get("recommended", False))
            if (r["source"], r["source_id"]) in quality
            else False,
            source_url=r.get("source_url") or "",
            hours=parse_duration_hours(r.get("duration")),
        )
        for r in rows
    ]
    # 有质量分在前（分高在前），未评估课程排后
    items.sort(key=lambda c: (c.quality_score is None, -(c.quality_score or 0.0)))
    return items if top_k is None else items[:top_k]
