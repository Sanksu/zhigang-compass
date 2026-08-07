"""多平台数据交叉验证（DA-M3-03，设计文档 §4.5）。

对归一化后同岗位的 JD 组做跨平台校验：
1. 技能一致性：技能 ≥2 独立源印证 → verified；单源技能进人工审核
2. 薪资异常：同岗位多平台月薪中位数差异 >50% → salary_outlier
3. 经验要求：跨平台经验值分歧度（众数口径标注）
4. 跨源置信度：数据源数量/一致性/时效三因子加权；单源岗位置信度低、入图谱需复核

聚合口径：`normalize_position_name`（dictionary.py）归一的岗位名分组，
公司名模糊匹配（SBERT 0.85）为后续增强项，当前以岗位名+来源为准。
"""

import re
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Optional

from app.services.data_quality.schemas import CrossValidationResult

# ── 设计文档 §4.5 / 附录阈值 ──
VERIFIED_MIN_SOURCES = 2       # 技能 ≥2 独立源印证
CONFIDENCE_GRAPH_MIN = 0.6     # 数据源入图谱置信度下限
SALARY_OUTLIER_RATIO = 1.5     # 薪资中位数差异 >50%（max/min > 1.5）
FRESH_DAYS = 7                 # 时效新鲜窗口（天）

# 置信度三因子权重（设计文档 §4.5：数据源数量/跨源一致性/时效）
W_SOURCE = 0.4
W_CONSISTENCY = 0.4
W_FRESHNESS = 0.2

# 汇率（美元→人民币，折衷常数，仅用于薪资异常的相对比较口径）
_USD_RATE = 7.0
_DAYS_PER_MONTH = 21           # 日薪 → 月薪（工作日）

# 薪资解析正则：(低, 高, 类型)。类型决定月薪折算。
# 注意：字符类中的 - 是范围符，分隔符一律用非捕获组 (?:-|~|至)。
# - cn_wan: "1.5-3万·14薪" / "1.1-2万" → 万/月
# - cn_k: "50-80K" / "14-16K·16薪" → 千元/月
# - cn_day: "200-220元/天" → 元/天 × 21
# - usd_year: "$171,000.00 - $260,000.00 / year" / "USD 175000.0-250000.0/年"
_SEP = r"(?:-|~|至)"
_SALARY_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(rf"([\d.]+)\s*{_SEP}\s*([\d.]+)\s*万"), "cn_wan"),
    (re.compile(rf"([\d.]+)\s*{_SEP}\s*([\d.]+)\s*K\b", re.IGNORECASE), "cn_k"),
    (re.compile(rf"([\d.]+)\s*{_SEP}\s*([\d.]+)\s*元/天"), "cn_day"),
    (re.compile(rf"USD\s*\$?([\d,]+(?:\.\d+)?)\s*{_SEP}\s*\$?([\d,]+(?:\.\d+)?)", re.IGNORECASE), "usd_year"),
    (re.compile(rf"([\d,]+(?:\.\d+)?)\s*{_SEP}\s*\$?([\d,]+(?:\.\d+)?)\s*/\s*year", re.IGNORECASE), "usd_year"),
]
# 单值格式：up to $120/hour、$171,000.00、$120/hour
_SINGLE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"up to\s*\$\s*([\d,]+(?:\.\d+)?)\s*k", re.IGNORECASE), "usd_year"),
    (re.compile(r"up to\s*\$?([\d.]+)\s*/hour", re.IGNORECASE), "usd_hour"),
    (re.compile(r"\$\s*([\d,]+(?:\.\d+)?)\s*/hour", re.IGNORECASE), "usd_hour"),
]

# "up to 170k"（无货币符号）：国际源按年薪（usd_year），国内源按千元/月（cn_k）
_UP_TO_K_RE = re.compile(r"up to\s*\$?([\d,]+(?:\.\d+)?)\s*k", re.IGNORECASE)
_INTL_SOURCES = {"indeed", "monster", "glassdoor", "linkedin_public"}


def _to_monthly_cny(value: float, kind: str) -> float:
    """按类型折算为月薪（元/月）。"""
    if kind == "cn_wan":
        return value * 10000
    if kind == "cn_k":
        return value * 1000
    if kind == "cn_day":
        return value * _DAYS_PER_MONTH
    if kind == "usd_year":
        return value / 12 * _USD_RATE
    if kind == "usd_hour":
        return value * 8 * _DAYS_PER_MONTH * _USD_RATE
    raise ValueError(f"未知薪资类型: {kind}")


def parse_monthly_salary(raw: str, source: str | None = None) -> Optional[float]:
    """解析薪资文本为月薪中值（元/月）；无法解析返回 None。

    支持的格式（对齐实际数据）：
    "1.5-3万·14薪"、"50-80K"、"200-220元/天"、"$171,000.00 - $260,000.00 / year"、
    "USD 175000.0-250000.0/年"、"up to $120/hour"、"up to 170k"

    source 用于 `up to 170k` 无货币符号时的口径判定：国际源（indeed/monster/
    glassdoor/linkedin_public）按年薪折算（usd_year），国内源按千元/月（cn_k），
    避免国际年薪被误判为国内月薪导致薪资异常误报。
    """
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip().replace(",", "")
    if not text:
        return None
    for pattern, kind in _SALARY_PATTERNS:
        m = pattern.search(text)
        if m:
            low, high = float(m.group(1)), float(m.group(2))
            return round(_to_monthly_cny((low + high) / 2, kind), 2)
    m = _UP_TO_K_RE.search(text)
    if m:
        has_dollar = "$" in m.group(0)
        kind = "usd_year" if has_dollar or (source or "") in _INTL_SOURCES else "cn_k"
        return round(_to_monthly_cny(float(m.group(1)), kind), 2)
    for pattern, kind in _SINGLE_PATTERNS:
        m = pattern.search(text)
        if m:
            return round(_to_monthly_cny(float(m.group(1)), kind), 2)
    return None


def _freshness_factor(crawled_at: str, reference: datetime) -> float:
    """时效因子：近 7 天 1.0 / 7-30 天 0.8 / 更旧 0.6（解析失败视为新鲜）。

    crawled_at 缺时区时按 +08:00（CST）假定——国内 crawler 写入为 naive 本地时间，
    与 aware reference 直接相减会抛 TypeError（落入 except 恒判新鲜）。
    """
    try:
        crawled = datetime.fromisoformat(crawled_at)
        if crawled.tzinfo is None:
            crawled = crawled.replace(tzinfo=timezone(timedelta(hours=8)))
    except (ValueError, TypeError):
        return 1.0
    days = (reference - crawled).days
    if days <= FRESH_DAYS:
        return 1.0
    if days <= 30:
        return 0.8
    return 0.6


def _skills_of(record: dict) -> list[str]:
    """JD 抽取结果技能名（requirements 优先，与 tasks._skills_of 同口径）。"""
    ext = (record.get("snapshot") or {}).get("extraction") or {}
    reqs = ext.get("requirements") or []
    if reqs:
        return [r.get("skill_name", "") for r in reqs if r.get("skill_name")]
    return [s.get("name", "") for s in (ext.get("skills") or []) if s.get("name")]


def build_position_groups(records: list[dict]) -> dict[str, list[dict]]:
    """按归一化岗位名聚合 JD 记录。

    返回 {归一化岗位名: [record, ...]}。岗位名归一化失败（空串）的记录丢弃。
    """
    from app.services.extraction.dictionary import normalize_position_name

    groups: dict[str, list[dict]] = {}
    for rec in records:
        ext = (rec.get("snapshot") or {}).get("extraction") or {}
        pos = normalize_position_name(ext.get("position_name") or "")
        if not pos:
            continue
        groups.setdefault(pos, []).append(rec)
    return groups


def validate_group(position_name: str, group: list[dict]) -> CrossValidationResult:
    """校验单个岗位组（纯函数）。

    - 技能一致性：技能 → 独立源集合；≥2 源技能计 verified
    - 薪资：可解析月薪中值集合，max/min > 1.5 → salary_outlier
    - 经验：snapshot.experience 唯一值占组比例 → 分歧度
    - 置信度：三因子加权（数据源数/一致性/时效），单源自动低置信
    """
    sources = {rec.get("source") or "" for rec in group} - {""}
    source_count = len(sources)

    skill_sources: dict[str, set[str]] = {}
    for rec in group:
        src = rec.get("source") or ""
        for skill in _skills_of(rec):
            skill_sources.setdefault(skill, set()).add(src)

    total_skills = len(skill_sources)
    verified_skills = {
        skill for skill, srcs in skill_sources.items() if len(srcs) >= VERIFIED_MIN_SOURCES
    }
    verified_ratio = (
        len(verified_skills) / total_skills if total_skills else 0.0
    )
    unverified_skills = sorted(
        skill for skill, srcs in skill_sources.items() if len(srcs) < VERIFIED_MIN_SOURCES
    )

    # 薪资：组内各 JD 可解析月薪中值（过滤非正数，防除零/无意义中位数）
    salaries = []
    for rec in group:
        ext = (rec.get("snapshot") or {}).get("extraction") or {}
        raw = ext.get("salary_range") or (rec.get("snapshot") or {}).get("salary")
        value = parse_monthly_salary(raw, rec.get("source"))
        if value and value > 0:
            salaries.append(value)
    salary_median = median(salaries) if salaries else None
    salary_outlier = (
        len(salaries) >= 2 and (max(salaries) / min(salaries)) > SALARY_OUTLIER_RATIO
    )

    # 经验分歧度：有经验值记录中唯一值占比（完全一致 0，全不同 1）。
    # 缺经验值记录不进分母，避免稀释分歧度（审查修复）。
    exp_values = [
        (rec.get("snapshot") or {}).get("experience") or ""
        for rec in group
    ]
    exp_values = [v for v in exp_values if v]
    exp_divergence = len(set(exp_values)) / len(exp_values) if exp_values else 0.0

    # 置信度：数据源数 / 一致性 / 时效（取组内最新 crawled_at）
    newest_crawled = max(
        (rec.get("crawled_at") or "" for rec in group),
        default="",
    )
    freshness = _freshness_factor(newest_crawled, datetime.now(timezone(timedelta(hours=8))))
    source_factor = min(source_count / 3.0, 1.0)
    confidence = round(
        W_SOURCE * source_factor + W_CONSISTENCY * verified_ratio + W_FRESHNESS * freshness,
        3,
    )

    return CrossValidationResult(
        position_name=position_name,
        jd_count=len(group),
        source_count=source_count,
        sources=sorted(sources),
        verified=bool(verified_skills),
        confidence=confidence,
        verified_skill_ratio=round(verified_ratio, 3),
        unverified_skills=unverified_skills,
        salary_median=salary_median,
        salary_outlier=salary_outlier,
        experience_divergence=round(exp_divergence, 3),
    )
