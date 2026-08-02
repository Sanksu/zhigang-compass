"""学习课程加载（AL-M4-03，设计文档 §4.6）。

链路：图谱 (Skill)-[:LEARNABLE_VIA]->(Course) → 关联 PostgreSQL
course_raw.snapshot["quality"]（DA-M4-01 课程质量评估产物）→ 按质量分降序取 Top-3。
未评估课程（质量分缺失）排在有分课程之后，属于合法状态不阻断。
"""

import re

from sqlalchemy import select, tuple_

from app.core.database import async_session_factory, neo4j_driver
from app.models.raw import CourseRaw
from app.services.learning_path.schemas import CourseRecommendation

# 学习路径按质量分取 Top-3（设计文档 §4.6）
_TOP_COURSES = 3

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


async def load_courses_for_skill(
    skill_id: str,
    skill_name: str,
    top_k: int | None = _TOP_COURSES,
) -> list[CourseRecommendation]:
    """查询技能可学习课程，按质量分降序返回（top_k=None 返回全量）。

    Args:
        skill_id: 图谱技能 ID（可空串，按 name 匹配兜底）
        skill_name: 技能名
        top_k: 返回条数，None 为全量；缺省 Top-3（设计文档 §4.6）
    """
    with neo4j_driver.session() as session:
        rows = [
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
