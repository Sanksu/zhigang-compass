"""岗位技能熟练度等级的统一规范化与判定语义。"""

from __future__ import annotations

from typing import Final

CANONICAL_PROFICIENCY_LEVELS: Final[frozenset[str]] = frozenset(
    {"初级", "中级", "高级", "专家"}
)

# 仅接受岗位技能要求语义明确的同义等级；未命中时必须由调用方显式处理，
# 禁止把未知等级当作“无等级要求”。
_PROFICIENCY_ALIASES: Final[dict[str, str]] = {
    "初级": "初级",
    "初等": "初级",
    "入门": "初级",
    "基础": "初级",
    "了解": "初级",
    "熟悉": "初级",
    "中级": "中级",
    "中等": "中级",
    "掌握": "中级",
    "高级": "高级",
    "资深": "高级",
    "精通": "高级",
    "专家": "专家",
}

_PROFICIENCY_FACTORS: Final[dict[str, dict[int, float]]] = {
    "初级": {1: 0.85, 2: 1.0, 3: 1.0},
    "中级": {1: 0.60, 2: 1.0, 3: 1.0},
    "高级": {1: 0.30, 2: 0.60, 3: 1.0},
    "专家": {1: 0.30, 2: 0.60, 3: 0.85},
}


def normalize_proficiency_level(level: object) -> str | None:
    """将岗位技能熟练度规范为初级/中级/高级/专家。

    空值表示岗位未声明熟练度要求；非空但未识别的值返回 ``None``，调用方不得
    将其与空值混同为完全满足。
    """
    if not isinstance(level, str):
        return None
    return _PROFICIENCY_ALIASES.get(level.strip())


def has_proficiency_requirement(level: object) -> bool:
    """是否显式提供了岗位技能熟练度字段（含无法识别的非法值）。"""
    return isinstance(level, str) and bool(level.strip())


def proficiency_factor(level: object, candidate: int | None) -> float:
    """按既定评分矩阵返回熟练度满足度。

    未声明岗位等级或候选人熟练度缺失时不惩罚；非空未知等级返回 0.0，防止被
    静默视为完全满足。
    """
    if not has_proficiency_requirement(level) or candidate is None:
        return 1.0
    normalized = normalize_proficiency_level(level)
    if normalized is None:
        return 0.0
    return _PROFICIENCY_FACTORS[normalized].get(candidate, 0.0)


def proficiency_is_weak(level: object, candidate: int | None) -> bool:
    """岗位声明等级但候选人不能获得该矩阵行的完全满足时是否为 weak。"""
    return has_proficiency_requirement(level) and proficiency_factor(level, candidate) < 1.0
