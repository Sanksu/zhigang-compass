"""数据多样性报告指标（DA-M4-02）。

从四类 raw 表（jd/course/paper/community）聚合多样性指标：
- 源覆盖：每类数据的平台/来源分布
- 岗位多样性：唯一岗位数、Top-N 岗位、每岗位平均技能数、技能集中度 CR10
- 课程多样性：平台分布、唯一技能标签数
- 去重率：fingerprint 唯一性

所有函数为纯函数（输入 dict 列表），便于单测与真实数据复用。
"""

from collections import Counter


def source_distribution(rows: list[dict]) -> list[dict]:
    """来源分布：rows: [{source}] → [{source, count}] 按条数降序。"""
    counter: Counter[str] = Counter((r.get("source") or "").strip() or "unknown" for r in rows)
    return [{"source": name, "count": cnt} for name, cnt in counter.most_common()]


def dedup_stats(rows: list[dict]) -> dict:
    """去重率：rows: [{fingerprint}] → {total, unique, duplicates, duplicate_rate}。

    duplicate_rate = (total - unique) / total，0 表示无重复。
    """
    total = len(rows)
    unique = len({r.get("fingerprint") for r in rows if r.get("fingerprint")})
    duplicates = max(total - unique, 0)
    return {
        "total": total,
        "unique": unique,
        "duplicates": duplicates,
        "duplicate_rate": round(duplicates / total, 4) if total else 0.0,
    }


def _cr10(skill_counter: Counter, total_mentions: int) -> float:
    """技能集中度 CR10：Top-10 技能提及量占比（0-1，越低越分散）。"""
    if total_mentions <= 0:
        return 0.0
    top = sum(cnt for _, cnt in skill_counter.most_common(10))
    return round(top / total_mentions, 4)


def position_diversity(items: list[dict], top_n: int = 10) -> dict:
    """岗位多样性：items: [{position_name, skills: [str]}]。

    返回：唯一岗位数、每岗位平均技能数、Top-N 岗位、技能提及总量、唯一技能数、CR10。
    岗位名空串不计数；技能列表为空按 0 技能计入平均。
    """
    pos_counter: Counter[str] = Counter()
    skill_counter: Counter[str] = Counter()
    skill_per_pos: list[int] = []

    for item in items:
        name = (item.get("position_name") or "").strip()
        if not name:
            continue
        pos_counter[name] += 1
        skills = item.get("skills") or []
        skill_per_pos.append(len(skills))
        skill_counter.update(s for s in skills if s)

    total_positions = sum(pos_counter.values())
    skill_mentions = sum(skill_counter.values())
    avg_skills = round(sum(skill_per_pos) / len(skill_per_pos), 2) if skill_per_pos else 0.0

    return {
        "total_positions": total_positions,
        "unique_positions": len(pos_counter),
        "avg_skills_per_position": avg_skills,
        "skill_mentions": skill_mentions,
        "unique_skills": len(skill_counter),
        "cr10": _cr10(skill_counter, skill_mentions),
        "top_positions": [
            {"name": name, "count": cnt} for name, cnt in pos_counter.most_common(top_n)
        ],
    }


def course_diversity(items: list[dict]) -> dict:
    """课程多样性：items: [{platform, skills: [str]}]。

    返回：课程总数、平台分布、唯一技能标签数。
    """
    platform_counter: Counter[str] = Counter((r.get("platform") or "").strip() or "unknown" for r in items)
    skill_tags = {s for r in items for s in (r.get("skills") or []) if s}
    return {
        "total_courses": len(items),
        "platforms": [
            {"platform": name, "count": cnt} for name, cnt in platform_counter.most_common()
        ],
        "unique_skill_tags": len(skill_tags),
    }
