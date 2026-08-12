"""岗位白名单审计：验证图谱岗位名全部来自合法路径，无剥壳残留碎片。

背景（2026-08-12 白名单改造）：LLM 对复杂格式岗位名解析不稳定，产出剥壳碎片
（"人事"、"智能"、"激光工艺"、"公司：外企德科"等）直接入图。此前靠
_POSITION_STOPWORDS 逐个打补丁（黑名单膨胀）。白名单改造后 normalize_position_name
对纯中文剥壳残留核心词要求命中 _POSITION_WHITELIST（关键词族/路由族/分析师族/翻译值）
才返回，否则返回空串——碎片从源头拦截。

本脚本周期性运行，枚举图谱全部 Position 节点，判定每个岗位名的归一化来源路径：

- standard：命中 _POSITION_KEYWORDS 关键词族（合法）
- routed：命中 _POSITION_SKILL_ROUTING 技能路由 family（合法）
- translated：英文翻译 _EN_POSITION_MAP 结果（合法）
- tech+base：_TECH_STACKS 前缀 + 标准族组合（合法）
- analyst：分析师细分族（合法）
- **residual（残留核心词）：不属于以上任何合法集合 → 白名单改造后已被清空（应为 0）**

residual > 0 说明出现白名单漏登记的合法低频岗位（用 audit_future_positions 识别后登记）。

只读，不写库、不改代码。用法：cd backend && python -m scripts.audit_position_whitelist
"""

from __future__ import annotations

from collections import Counter

from app.core.database import neo4j_driver
from app.services.extraction.dictionary import (
    _ANALYST_SUB_FAMILIES,
    _EN_POSITION_MAP,
    _POSITION_KEYWORDS,
    _POSITION_SKILL_ROUTING,
    _TECH_STACKS,
    normalize_position_name,
)

# 合法岗位族集合（白名单语义，程序化推导，不硬编码）
_STANDARD_NAMES: set[str] = {standard for _, standard in _POSITION_KEYWORDS}
_ROUTED_NAMES: set[str] = {family for _, family in _POSITION_SKILL_ROUTING}
_ANALYST_NAMES: set[str] = {standard for _, standard in _ANALYST_SUB_FAMILIES}
_TRANSLATED_NAMES: set[str] = set(_EN_POSITION_MAP.values())
_TECH_PREFIXES: tuple[str, ...] = tuple(sorted(
    {display for _, display in _TECH_STACKS}, key=len, reverse=True
))
# 失真兜底族不作为聚合目的地，但归一化返回值仍可能是"路由空"或族名；路由 family
# 已含在 _ROUTED_NAMES。分析师兜底"分析师"在 _ANALYST_NAMES 尾部？需显式补
_LEGAL = _STANDARD_NAMES | _ROUTED_NAMES | _ANALYST_NAMES | _TRANSLATED_NAMES | {"分析师"}


def _classify(name: str, norm: str) -> str:
    """判定归一化结果属于哪条来源路径。

    返回 standard/routed/translated/tech_base/analyst/residual。
    norm 为空（当前就不入图）→ "empty"。
    """
    if not norm:
        return "empty"
    if norm in _STANDARD_NAMES:
        return "standard"
    if norm in _ROUTED_NAMES:
        return "routed"
    if norm in _TRANSLATED_NAMES:
        return "translated"
    if norm in _ANALYST_NAMES or norm == "分析师":
        return "analyst"
    # tech + base 组合：去任一 tech 前缀后剩余部分落在合法集合
    for tech in _TECH_PREFIXES:
        if norm.startswith(tech):
            rest = norm[len(tech):].strip()
            if rest in _LEGAL or rest == name.strip():
                return "tech_base"
    return "residual"


def _main() -> None:
    with neo4j_driver.session() as session:
        rows = session.run(
            "MATCH (p:Position) RETURN p.name AS name "
            "ORDER BY p.name"
        ).data()
    names = [r["name"] for r in rows if r.get("name")]
    print(f"图谱 Position 节点总数：{len(names)}")

    categories: Counter = Counter()
    residual_rows: list[tuple[str, str]] = []
    for raw in names:
        norm = normalize_position_name(raw)
        cat = _classify(raw, norm)
        categories[cat] += 1
        if cat == "residual":
            residual_rows.append((raw, norm))

    print("\n来源路径分布：")
    for cat in ("standard", "routed", "translated", "analyst", "tech_base", "residual", "empty"):
        print(f"  {cat:<12} {categories[cat]}")

    print(f"\n残留核心词候选（改闸门后会被清空的岗位名）：{len(residual_rows)}")
    for raw, norm in residual_rows:
        print(f"  {raw!r}  → 归一化={norm!r}")


if __name__ == "__main__":
    _main()
