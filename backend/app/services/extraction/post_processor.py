"""抽取结果后处理：中文后缀清洗 + 白名单过滤 + 去重。

设计文档 5.2 节：中文后缀清洗（去除"系统/框架/技术/工程师"等 30+ 后缀）。
"""

import re
from app.services.extraction.schemas import JDExtractionResult, SkillExtracted
from app.services.extraction.dictionary import (
    SOFT_SKILL_WHITELIST,
    SKILL_STOPWORDS,
    normalize_skill,
)

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
    """清洗技能名称中的中文后缀。

    软技能白名单整体跳过（"项目管理"不以"管理"为后缀退化），
    其余技能按后缀表剥除（"前端开发"→"前端"）。
    """
    if name in SOFT_SKILL_WHITELIST:
        return name
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
    3. 黑名单剔除（行业/业务领域词，防 LLM 幻觉技能入图）
    4. 去重
    """
    # skills 与 requirements 共用同一清洗规则：别名 → 后缀清洗 → 黑名单剔除
    def _clean(name: str) -> str:
        return clean_skill_name(normalize_skill(name))

    result.skills = [
        SkillExtracted(name=_clean(s.name), category=s.category, description=s.description)
        for s in result.skills
        if _clean(s.name) and _clean(s.name) not in SKILL_STOPWORDS
    ]
    result.skills = dedup_skills(result.skills)

    # 软技能：仅保留岗位本体白名单（LLM 越界输出在此拦截，防非白名单词入岗位本体）
    seen_soft: set[str] = set()
    cleaned_soft: list[str] = []
    for s in result.soft_skills:
        name = clean_skill_name(normalize_skill(s)).strip()
        if not name or name in seen_soft or name not in SOFT_SKILL_WHITELIST:
            continue
        seen_soft.add(name)
        cleaned_soft.append(name)
    result.soft_skills = cleaned_soft

    for tool in result.tools:
        tool.name = normalize_skill(tool.name)

    # requirements 与 skills 使用同一清洗规则（避免非标准技能名入图），
    # 并按 (技能, 必要性) 去重，与 skills 去重口径一致
    cleaned_reqs = []
    seen: set[tuple[str, str]] = set()
    for req in result.requirements:
        name = _clean(req.skill_name)
        if not name or name in SKILL_STOPWORDS:
            continue
        key = (name.lower(), req.necessity)
        if key in seen:
            continue
        seen.add(key)
        req.skill_name = name
        cleaned_reqs.append(req)
    result.requirements = cleaned_reqs

    return result
