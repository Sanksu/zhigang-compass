"""技能先修字典加载与先修链展开（AL-M4-03）。

图谱当前无 PREREQUISITE_OF 先修关系边，先修链以人工维护字典兜底
（configs/skill_prerequisites.yaml，与设计文档 §5.3 "人工词典兜底" 哲学一致）。
"""

from functools import lru_cache
from pathlib import Path

import yaml

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "skill_prerequisites.yaml"
_DEFAULT_HOURS_PER_SKILL = 30.0


@lru_cache(maxsize=1)
def load_prerequisite_config() -> dict:
    """加载先修字典（进程内缓存，配置变更需重启生效）。"""
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def base_hours(skill_name: str) -> float:
    """技能基础学时（小时）：字典内可逐技能覆盖，未收录用默认值。"""
    cfg = load_prerequisite_config()
    default = float(cfg.get("default_hours_per_skill", _DEFAULT_HOURS_PER_SKILL))
    entry = (cfg.get("skills") or {}).get(skill_name) or {}
    return float(entry.get("hours", default))


def prerequisite_chain(skill_name: str) -> list[str]:
    """展开技能先修链（拓扑序，先修在前，不含目标技能本身）。

    未收录技能返回空链；环引用通过 visited 集合防护；链深天然受字典深度约束。
    """
    cfg = load_prerequisite_config()
    skills = cfg.get("skills") or {}

    result: list[str] = []
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visited:
            return
        visited.add(name)
        for pre in (skills.get(name) or {}).get("prerequisites", []) or []:
            visit(pre)
        if name != skill_name:
            result.append(name)

    visit(skill_name)
    return result
