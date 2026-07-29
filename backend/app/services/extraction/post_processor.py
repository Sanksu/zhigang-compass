"""抽取结果后处理：中文后缀清洗 + 白名单过滤 + 去重。

设计文档 5.2 节：中文后缀清洗（去除"系统/框架/技术/工程师"等 30+ 后缀）。
"""

import re
from app.services.extraction.schemas import JDExtractionResult, SkillExtracted
from app.services.extraction.dictionary import normalize_skill, SKILL_WHITELIST

# 需去除的中文后缀（按长度降序排列，优先匹配长后缀）
SUFFIXES = sorted([
    "工程师", "技术", "系统", "框架", "平台", "工具", "软件", "开发",
    "设计", "管理", "应用", "服务", "方案", "产品", "项目", "算法",
    "架构", "引擎", "组件", "中间件", "协议", "标准", "接口", "协议",
], key=len, reverse=True)

_SKILL_SUFFIX_RE = re.compile(
    f"({'|'.join(re.escape(s) for s in SUFFIXES)})$"
)


def clean_skill_name(name: str) -> str:
    """清洗技能名称中的中文后缀。"""
    name = _SKILL_SUFFIX_RE.sub("", name).strip()
    return name


def dedup_skills(skills: list[SkillExtracted]) -> list[SkillExtracted]:
    """按 name 去重（保留首次出现）。"""
    seen: set[str] = set()
    result = []
    for s in skills:
        key = s.name.lower()
        if key not in seen:
            seen.add(key)
            result.append(s)
    return result


def post_process(result: JDExtractionResult) -> JDExtractionResult:
    """执行完整后处理管线：
    1. 别名归一化
    2. 后缀清洗
    3. 去重
    4. 白名单标记
    """
    for skill in result.skills:
        skill.name = normalize_skill(skill.name)
        skill.name = clean_skill_name(skill.name)
    for tool in result.tools:
        tool.name = normalize_skill(tool.name)

    result.skills = dedup_skills(result.skills)

    # 对 requirements 也执行归一化
    for req in result.requirements:
        req.skill_name = normalize_skill(req.skill_name)

    return result
