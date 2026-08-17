"""岗位白名单审计：自动检测被拒但可能合法的低频岗位，生成白名单建议。

背景：normalize_position_name 的"剥壳残留即返回"是碎片入图通道（"人事"/"智能"/
"公司：外企德科"），根治方案改为白名单语义后，需要持续识别"被当前规则拒绝但
实际合法"的低频岗位（如 IT系统管理员、首席统计师），并区分真正的碎片。

拒绝原因判定（避免误报设计行为）：
- routed_no_skill：命中失真兜底族（软件开发工程师/算法工程师…）但无技能路由
  → 设计行为（2026-08-09 治理），不加白名单
- blocked：命中停用词 → 已拦截，跳过
- residual / fragment / en_untranslated：真正需要审计的候选

分类输出三组建议：
- 建议加入白名单：结构完整、含岗位后缀、多份 JD 佐证 → 合法低频岗位
- 建议加入停用词：公司名残留/泛词/剥壳碎片 → 直接拦截
- 存疑待人工：信号不足，需人工复核

只读，不写库不改代码。用法：cd backend && python -m scripts.audit_future_positions
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter

from sqlalchemy import select

from app.core.database import async_session_factory
from app.models.raw import JDRaw
from app.services.extraction.dictionary import (
    _GENERIC_ROUTED_FAMILIES,
    _POSITION_STOPWORDS,
    _normalize_base,
    _translate_en_position,
    normalize_position_name,
)

# 岗位后缀信号：以这些词结尾大概率是合法岗位（含 _POSITION_SUFFIX_RE 的变体，
# 补低频岗位常见后缀如"教师/管理员/助理"）
_POSITION_SUFFIXES = (
    "工程师", "技术员", "程序员", "研发人员", "研发", "开发", "设计师", "经理",
    "主管", "负责人", "专员", "分析师", "科学家", "研究员", "专家", "顾问",
    "助理", "管理员", "教师", "讲师", "教练", "审计师", "会计师", "律师", "医师",
    "架构师", "技师", "运营", "编辑", "作者",
)

# 泛词碎片信号：无信息量的剥壳残留（短且无后缀的常见碎片）
_GENERIC_FRAGMENTS = {
    "人事", "行政", "智能", "技术", "后台", "业务", "产品", "数据", "研发", "开发",
    "运营", "管理", "系统", "平台", "架构", "算法", "测试", "前端", "后端", "网络",
    "实施", "咨询", "招聘", "销售", "客服", "文员", "助理", "专员",
}

# 公司名残留信号：岗位名中出现这些词极可能是公司名/组织名当岗位名
_COMPANY_MARKERS = ("公司", "集团", "有限", "股份", "科技公司", "：")


def _has_suffix(name: str) -> bool:
    return any(name.endswith(s) for s in _POSITION_SUFFIXES)


def _reject_reason(raw: str) -> str:
    """判定岗位名被 normalize_position_name 拒绝的原因。

    Returns:
        "routed_no_skill" / "blocked" / "residual" / "en_untranslated" / "other"
    """
    if raw in _POSITION_STOPWORDS:
        return "blocked"
    # 英文翻译路径：翻译后落入兜底族 → 无技能路由空（设计行为）
    translated = _translate_en_position(raw)
    if translated:
        base = _normalize_base(translated)
        if base in _GENERIC_ROUTED_FAMILIES:
            return "routed_no_skill"
        return "other"  # 理论上 translate 后不会空（norm 非空），防御
    # 中文路径：剥括号/级别前缀后归一化，落入兜底族 → 无技能路由空
    base = re.sub(r"[（(].*?[)）]", "", raw).strip()
    base = re.sub(r"^(初级|中级|高级|资深|专家|助理|实习|见习|应届|研发)", "", base).strip()
    base = _normalize_base(base)
    if base in _GENERIC_ROUTED_FAMILIES:
        return "routed_no_skill"
    if not base:
        return "other"
    # 剥壳残留非空但 normalize 返回空 → 技能词/软技能白名单拦截（P1 设计行为）
    # 核对：_SKILL_WHITELIST_LOWER 命中或软技能白名单命中 → 技能被抽成岗位
    from app.services.extraction.dictionary import (
        SOFT_SKILL_WHITELIST,
        _SKILL_WHITELIST_LOWER,
    )

    if base.lower() in _SKILL_WHITELIST_LOWER or base in SOFT_SKILL_WHITELIST:
        return "skill_blocked"
    return "residual"


def _looks_like_fragment(name: str) -> bool:
    """碎片判定：公司名残留 / 冒号残留 / 泛词无后缀 / 过短。"""
    if any(m in name for m in _COMPANY_MARKERS):
        return True
    if len(name) < 2:
        return True
    if not _has_suffix(name) and name in _GENERIC_FRAGMENTS:
        return True
    return False


async def _run() -> None:
    async with async_session_factory() as s:
        rows = (await s.scalars(select(JDRaw))).all()

    # 归一化结果为空的岗位名 → 计数（去重后的唯一岗位名 + 总频次）
    rejected: Counter = Counter()
    for r in rows:
        snap = r.snapshot or {}
        if snap.get("_duplicate_of"):
            continue
        ext = snap.get("extraction") or {}
        if not ext:
            continue
        raw_name = (ext.get("position_name") or "").strip()
        if not raw_name:
            continue
        if not normalize_position_name(raw_name):
            rejected[raw_name] += 1

    print("=" * 70)
    print("岗位白名单审计：被拒岗位名分类建议")
    print("=" * 70)
    print(f"扫描 JD 总条数：{len(rows)}；归一化被拒岗位名唯一值：{len(rejected)}")

    # 按拒绝原因分流
    routed = [(n, c) for n, c in rejected.items() if _reject_reason(n) == "routed_no_skill"]
    blocked = [(n, c) for n, c in rejected.items() if _reject_reason(n) == "blocked"]
    candidates = [(n, c) for n, c in rejected.items() if _reject_reason(n) == "residual"]

    print(f"  其中设计行为（兜底族无技能路由）：{len(routed)}")
    print(f"  已拦截（停用词）：{len(blocked)}")
    print(f"  待审计候选（残留/碎片）：{len(candidates)}")

    # 候选分类
    suggest_whitelist: list[tuple[str, int]] = []
    suggest_stopword: list[tuple[str, int]] = []
    uncertain: list[tuple[str, int]] = []

    for name, cnt in candidates:
        if _looks_like_fragment(name):
            suggest_stopword.append((name, cnt))
        elif _has_suffix(name):
            if cnt >= 2:
                suggest_whitelist.append((name, cnt))
            else:
                uncertain.append((name, cnt))
        elif len(name) >= 3 and any("\u4e00" <= ch <= "\u9fff" for ch in name):
            uncertain.append((name, cnt))
        else:
            suggest_stopword.append((name, cnt))

    print(f"\n[1] 建议加入白名单（合法低频岗位，频次 ≥ 2）：{len(suggest_whitelist)}")
    for name, cnt in sorted(suggest_whitelist, key=lambda x: -x[1]):
        print(f"    {name!r}  ×{cnt}")

    print(f"\n[2] 建议加入停用词（碎片/公司名/泛词）：{len(suggest_stopword)}")
    for name, cnt in sorted(suggest_stopword, key=lambda x: -x[1]):
        print(f"    {name!r}  ×{cnt}")

    print(f"\n[3] 存疑待人工复核（单例或信号不足）：{len(uncertain)}")
    for name, cnt in sorted(uncertain, key=lambda x: -x[1]):
        print(f"    {name!r}  ×{cnt}")

    # 生成可粘贴的白名单建议
    if suggest_whitelist:
        print("\n" + "=" * 70)
        print("白名单建议代码片段（人工确认后粘贴到 _POSITION_KEYWORDS 尾部）：")
        for name, _ in sorted(suggest_whitelist, key=lambda x: -x[1]):
            print(f'    ((("{name}",), "{name}"),)')


def main() -> int:
    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    main()
