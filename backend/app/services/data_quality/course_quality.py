"""课程质量评估（DA-M4-01，设计文档 §4.6）。

六维加权评分，综合分 ≥ 0.65 进入推荐池：
| 维度 | 权重 | 口径 |
|------|------|------|
| 平台权威性 | 0.25 | 国际权威平台（coursera/edx）> 国内（icourse163） |
| 用户评分 | 0.20 | rating/5 归一化；rating 缺失（≤0）取中性 0.5 |
| 注册量与完成率 | 0.15 | enrollment 对数归一化（10 万 ≈ 满分） |
| 时效性 | 0.20 | 近 2 年满分，2-5 年线性衰减，>5 年降权 0.5 |
| 技能覆盖度 | 0.10 | skills 命中 SKILL_WHITELIST 白名单的比例 |
| 实战项目密度 | 0.10 | description 中实战/项目关键词密度 |

缺失口径：无法判断的维度取中性 0.5（不偏袒），可明确为空的维度（如
无技能标签、无简介）取 0。每月重跑评估管线，学习路径按质量分取 Top-3。
"""

import re
from datetime import date, datetime, timedelta, timezone
from math import log10

from app.services.data_quality.schemas import CourseQualityResult

# ── 六维权重（设计文档 §4.6）──
W_PLATFORM = 0.25
W_RATING = 0.20
W_ENROLLMENT = 0.15
W_RECENCY = 0.20
W_SKILL_COVERAGE = 0.10
W_PROJECT_DENSITY = 0.10

# 推荐池阈值
RECOMMEND_MIN_SCORE = 0.65

# 平台权威性映射（国际权威平台 > 国内）
_PLATFORM_AUTHORITY = {
    "coursera": 1.0,
    "edx": 1.0,
    "icourse163": 0.7,
}
# 未知平台中性分
_PLATFORM_UNKNOWN = 0.5

# 时效性窗口（天）：近 2 年满分，>5 年降权
_RECENCY_FULL_DAYS = 365 * 2
_RECENCY_DECAY_START_DAYS = 365 * 2   # 2 年后开始线性衰减
_RECENCY_DECAY_END_DAYS = 365 * 5     # 5 年后降到 0.5 并保持
_RECENCY_FLOOR = 0.5

# 注册量对数归一化：10 万注册 ≈ 满分
_ENROLLMENT_LOG_CAP = 100_000

# 实战/项目关键词（出现在 description 中即视为实战项目导向）
_PROJECT_KEYWORDS = (
    "实战", "项目", "案例", "实操", "实训", "开发", "构建", "实现",
    "project", "hands-on", "build", "implement", "case study",
)

# 课程简介缺失时的中性值
_DESCRIPTION_MISSING = 0.0
# 技能标签缺失时的中性值
_SKILLS_MISSING = 0.0

# 时间解析容错：start_date 支持多种格式，解析失败返回 None
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S", "%Y%m%d")

_TZ_CN = timezone(timedelta(hours=8))


def platform_authority(platform: str) -> float:
    """平台权威性（0-1）：已知平台查表，未知取中性。"""
    return _PLATFORM_AUTHORITY.get((platform or "").strip().lower(), _PLATFORM_UNKNOWN)


def rating_score(rating: float) -> float:
    """用户评分（0-1）：rating/5 归一化；缺失（≤0）取中性 0.5。"""
    try:
        r = float(rating)
    except (TypeError, ValueError):
        return 0.5
    if r <= 0:
        return 0.5
    return min(r / 5.0, 1.0)


def enrollment_score(enrollment: float) -> float:
    """注册量（0-1）：对数归一化，10 万 ≈ 满分；缺失/0 取 0。"""
    try:
        e = float(enrollment)
    except (TypeError, ValueError):
        return 0.0
    if e <= 0:
        return 0.0
    return min(log10(e) / log10(_ENROLLMENT_LOG_CAP), 1.0)


def recency_score(start_date: str | None) -> float:
    """时效性（0-1）：近 2 年满分，2-5 年线性衰减至 0.5，>5 年保持 0.5。"""
    dt = _parse_date(start_date)
    if dt is None:
        return 0.5  # 无开课日期视为无法判断，取中性
    days = (datetime.now(_TZ_CN).date() - dt).days
    if days <= _RECENCY_FULL_DAYS:
        return 1.0
    if days >= _RECENCY_DECAY_END_DAYS:
        return _RECENCY_FLOOR
    # 线性衰减：2 年(1.0) → 5 年(0.5)
    span = _RECENCY_DECAY_END_DAYS - _RECENCY_DECAY_START_DAYS
    decay = (days - _RECENCY_DECAY_START_DAYS) / span
    return round(1.0 - 0.5 * decay, 4)


def skill_coverage(skills: list[str]) -> float:
    """技能覆盖度（0-1）：skills 命中标准白名单的比例；无技能标签取 0。"""
    if not skills:
        return _SKILLS_MISSING
    from app.services.extraction.dictionary import SKILL_WHITELIST, normalize_skill

    hit = 0
    total = 0
    for raw in skills:
        name = normalize_skill(str(raw).strip())
        if not name:
            continue
        total += 1
        if name in SKILL_WHITELIST:
            hit += 1
    if total == 0:
        return 0.0
    return round(hit / total, 4)


def project_density(description: str) -> float:
    """实战项目密度（0-1）：description 中实战/项目关键词占比；无简介取 0。"""
    desc = (description or "").strip()
    if not desc:
        return _DESCRIPTION_MISSING
    low = desc.lower()
    hits = sum(1 for kw in _PROJECT_KEYWORDS if kw.lower() in low)
    # 密度：命中关键词数封顶 5，5 个及以上即满分
    return round(min(hits / 5.0, 1.0), 4)


def _parse_date(value: str | None) -> date | None:
    """解析 start_date，支持多种格式；失败返回 None。"""
    if not value:
        return None
    text = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def evaluate_course(course: dict) -> CourseQualityResult:
    """评估单门课程质量（六维加权，设计文档 §4.6）。

    Args:
        course: course_raw.snapshot 的 dict（含 title/rating/enrollment/
            start_date/skills/description/platform 等字段）

    Returns:
        CourseQualityResult：六维分 + 加权总分 + 是否进入推荐池
    """
    dims = {
        "platform": platform_authority(course.get("platform", "")),
        "rating": rating_score(course.get("rating", 0.0)),
        "enrollment": enrollment_score(course.get("enrollment", 0)),
        "recency": recency_score(course.get("start_date")),
        "skill_coverage": skill_coverage(course.get("skills") or []),
        "project_density": project_density(course.get("description", "")),
    }

    score = round(
        W_PLATFORM * dims["platform"]
        + W_RATING * dims["rating"]
        + W_ENROLLMENT * dims["enrollment"]
        + W_RECENCY * dims["recency"]
        + W_SKILL_COVERAGE * dims["skill_coverage"]
        + W_PROJECT_DENSITY * dims["project_density"],
        4,
    )

    return CourseQualityResult(
        title=course.get("title", ""),
        platform=course.get("platform", ""),
        platform_score=dims["platform"],
        rating_score=dims["rating"],
        enrollment_score=dims["enrollment"],
        recency_score=dims["recency"],
        skill_coverage_score=dims["skill_coverage"],
        project_density_score=dims["project_density"],
        quality_score=score,
        recommended=score >= RECOMMEND_MIN_SCORE,
    )
