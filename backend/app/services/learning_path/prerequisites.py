"""技能先修字典加载与先修链展开（AL-M4-03）。

图谱当前无 PREREQUISITE_OF 先修关系边，先修链以人工维护字典兜底
（configs/skill_prerequisites.yaml，与设计文档 §5.3 "人工词典兜底" 哲学一致）。
"""

from functools import lru_cache
from pathlib import Path

import yaml

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "skill_prerequisites.yaml"
_DEFAULT_HOURS_PER_SKILL = 30.0

# P1-2 学时分层（08-13 学习路径评审：默认 30h 一刀切系统性低估，hours 维度 0.42）。
# 未收录先修字典的技能按白名单类别给合理基准学时（单技能掌握普遍 40-100h 现实基准）。
_HOURS_BY_CATEGORY = {
    # 高复杂度：算法/模型/硬件/机器人需长时投入
    "AI/机器学习": 70.0, "算法": 70.0, "大数据": 70.0,
    "硬件/芯片": 70.0, "智能驾驶/机器人": 70.0, "音视频": 70.0,
    # 中：语言/框架/平台/安全/数据库/测试等
    "编程语言": 55.0, "前端": 55.0, "后端": 55.0, "云原生/DevOps": 55.0,
    "安全": 55.0, "数据库": 55.0, "测试": 55.0, "网络/协议": 55.0,
    "消息/中间件": 55.0, "游戏/数字孪生": 55.0, "移动/桌面": 55.0,
    # 低：数据/商业/软技能/基础
    "数据分析/商业": 40.0, "软技能": 40.0, "计算机基础": 40.0,
    "工程协作": 40.0, "数据处理": 40.0, "工程": 40.0,
}


@lru_cache(maxsize=1)
def _load_whitelist_categories() -> dict[str, str]:
    """白名单技能类别（skill_whitelist.yaml 单一事实源）：name → category。

    配置缺失/损坏为环境错误，fail-fast（学时分层依赖该配置，静默降级会
    使分层失效回退一刀切默认值）。
    """
    path = Path(__file__).resolve().parents[3] / "configs" / "skill_whitelist.yaml"
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"skill_whitelist.yaml 顶层结构异常: {path}")
    return {
        x.get("name"): x.get("category")
        for x in (data.get("skills") or [])
        if x.get("name")
    }


@lru_cache(maxsize=1)
def load_prerequisite_config() -> dict:
    """加载先修字典（进程内缓存，配置变更需重启生效）。"""
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def _canonical_skill(name: str) -> str:
    """技能规范名（同匹配引擎口径，剔别名/后缀/大小写差异）。

    先修字典键名按规范中文名维护，而图谱岗位技能名可能带别名/
    大小写/后缀差异（如 Graph 名 "Golang" vs 字典键 "Go"、"NSGs"
    vs "网络安全"）。查找前先归一化，避免"图谱名 ↔ 字典键"键名
    不一致导致先修链落空（AL-M5-06 先修字典键名一致性校验）。
    """
    from app.services.extraction.post_processor import canonical_skill_name

    return canonical_skill_name(name).strip().lower()


def _prereq_lookup_index() -> dict[str, str]:
    """先修字典键的匹配索引：规范名 / 原键小写 → 原键。

    同一规范名冲突时以配置中靠前的键为准（确定性）。索引缺失时才重建，
    避免每次查询全量扫描。
    """
    global _prereq_index_cache
    if _prereq_index_cache is not None:
        return _prereq_index_cache
    skills = (load_prerequisite_config().get("skills") or {})
    built: dict[str, str] = {}
    for key in skills:
        built.setdefault(key.strip().lower(), key)
        built.setdefault(_canonical_skill(key), key)
    _prereq_index_cache = built
    return built


# 先修匹配索引缓存（配置变更需重启；valid 标记为空字典时也复用）
_prereq_index_cache: dict[str, str] | None = None


def _resolve_skill_key(skill_name: str) -> str | None:
    """把请求的技能名解析为先修字典键（精确→小写→规范名逐级回退）。"""
    if not skill_name:
        return None
    skills = load_prerequisite_config().get("skills") or {}
    if skill_name in skills:
        return skill_name
    low = skill_name.strip().lower()
    if low in _prereq_lookup_index():
        return _prereq_lookup_index()[low]
    canon = _canonical_skill(skill_name)
    if canon in _prereq_lookup_index():
        return _prereq_lookup_index()[canon]
    return None


def base_hours(skill_name: str) -> float:
    """技能基础学时（小时）：字典逐技能覆盖 > 白名单类别分层 > 配置默认值。

    分层动机（08-13 评审）：默认 30h 一刀切使学时维度系统性低估（hours 0.42）；
    未收录技能按白名单类别给合理基准（AI/算法 70h、语言/框架 55h、数据/软技能 40h）。
    AL-M5-06：键名先经 _resolve_skill_key 归一（图谱名 ↔ 字典键一致性）。
    """
    cfg = load_prerequisite_config()
    default = float(cfg.get("default_hours_per_skill", _DEFAULT_HOURS_PER_SKILL))
    key = _resolve_skill_key(skill_name) or skill_name
    entry = (cfg.get("skills") or {}).get(key) or {}
    if "hours" in entry:
        return float(entry["hours"])
    category = _load_whitelist_categories().get(skill_name)
    if category:
        return _HOURS_BY_CATEGORY.get(category, default)
    return default


def prerequisite_chain(skill_name: str) -> list[str]:
    """展开技能先修链（拓扑序，先修在前，不含目标技能本身）。

    未收录技能返回空链；环引用通过 visited 集合防护；链深天然受字典深度约束。
    AL-M5-06：键名先经 _resolve_skill_key 归一（图谱名 ↔ 字典键一致性）。
    """
    cfg = load_prerequisite_config()
    skills = cfg.get("skills") or {}

    result: list[str] = []
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visited:
            return
        visited.add(name)
        key = _resolve_skill_key(name) or name
        for pre in (skills.get(key) or {}).get("prerequisites", []) or []:
            visit(pre)
        if name != skill_name:
            result.append(name)

    visit(skill_name)
    return result
