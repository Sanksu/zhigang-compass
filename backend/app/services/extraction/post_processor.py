"""抽取结果后处理：中文后缀清洗 + 白名单过滤 + 去重。

设计文档 5.2 节：中文后缀清洗（去除"系统/框架/技术/工程师"等 30+ 后缀）。
"""

import re
from app.services.extraction.schemas import JDExtractionResult, REQUIRESRelation, SkillExtracted
from app.services.extraction.dictionary import (
    SKILL_WHITELIST,
    SOFT_SKILL_NOISE,
    SOFT_SKILL_WHITELIST,
    _SKILL_MODIFIERS,
    is_noise_skill,
    normalize_skill,
    normalize_tool_name,
    register_grey_skill,
)
from app.services.extraction.dictionary_data import SKILL_ALIAS

# 别名反向索引（标准名 → 别名列表）：词面守卫豁免——正文用同义词/缩写时
# （"LLM" vs "大语言模型"）技能名词面未出现但别名命中即保留
_ALIAS_REV: dict[str, list[str]] = {}
for _k, _v in SKILL_ALIAS.items():
    _ALIAS_REV.setdefault(_v.lower(), []).append(_k.lower())


def _text_has(low: str, name: str) -> bool:
    """词边界检查技能名或其别名是否出现在正文（小写）。"""
    n = name.lower()
    if re.search(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])", low):
        return True
    return any(a in low for a in _ALIAS_REV.get(n, []))


def lexical_guard(result: JDExtractionResult, jd_text: str) -> JDExtractionResult:
    """词面守卫（08-17 JD 解析收尾）：skills 中正文无词面（含别名豁免）的
    技能降级到 requirements(nice)——LLM 演绎的"正文未出现"技能不作必备
    （防噪音进 must 聚合）；正文语义推断词（"数据可视化" vs 正文职责表述）
    同样降级——盲审若收录需靠 skills(must) 口径对齐（fn 可接受）。

    仅作用于 LLM 路径结果（result.method == "llm"）：规则兜底 skills 来自
    正文扫描，但清洗后技能名可能与原文不同（"C语言"→"C"），词面校验会
    误伤——规则路径跳过守卫。
    """
    if result.method != "llm" or not result.skills or not jd_text:
        return result
    low = jd_text.lower()
    kept = [s for s in result.skills if _text_has(low, s.name)]
    demoted = [s.name for s in result.skills if not _text_has(low, s.name)]
    if not demoted:
        return result
    result.skills = kept
    existing = {r.skill_name for r in result.requirements}
    for name in demoted:
        if name not in existing:
            result.requirements.append(REQUIRESRelation(skill_name=name, necessity="nice"))
    return result

# 需去除的中文后缀（按长度降序排列，优先匹配长后缀）。
# 复用 dictionary._SKILL_MODIFIERS：normalize_skill 剥修饰词重查与 clean_skill_name
# 后缀清洗用同一词表，保证抽取与消费链路口径一致。
# 不含"服务"：剥除后产生碎片（"微服务"→"微"），且无合理剥除场景
SUFFIXES = sorted(_SKILL_MODIFIERS, key=len, reverse=True)

_SKILL_SUFFIX_RE = re.compile(
    f"({'|'.join(re.escape(s) for s in SUFFIXES)})$"
)


def clean_skill_name(name: str) -> str:
    """清洗技能名称中的中文后缀。

    软技能与白名单词整体跳过（"项目管理"不以"管理"为后缀退化，
    "操作系统"不以"系统"为后缀退化——P1-2 起白名单词整体保护，防剥成泛词碎片），
    其余技能按后缀表剥除（"前端开发"→"前端"）。
    """
    if name in SKILL_WHITELIST:
        return name
    name = _SKILL_SUFFIX_RE.sub("", name).strip()
    return name


def canonical_skill_name(name: str) -> str:
    """技能规范名：别名归一化 + 中文后缀清洗。

    抽取/聚合/匹配/简历各链路的统一技能规范化入口（原 clean_skill_name(normalize_skill())
    内联组合在 5+ 处重复），保证口径一致、防后续词表演化时各链路漂移。
    不含大小写归一：调用方按需自行 .lower()（如匹配比较、评测对齐）。
    """
    return clean_skill_name(normalize_skill(name))


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


def is_valid_skill_name(name: str) -> bool:
    """技能名校验：白名单/别名标准名保护 + 泛词/碎片拦截。

    除 SKILL_STOPWORDS 黑名单与单字符碎片外，复用 is_noise_skill 的泛词判定
    （模糊词、岗位名碎片、经验描述短语等白名单外无技能语义的噪音），拦截
    "实验/评估/移动/报表"这类 LLM 误抽泛词，降低技能图语义噪音（图谱快照审查
    报告问题 3）。白名单词与别名标准名整体保护，不误杀真实技能。
    """
    return not is_noise_skill(name)


def post_process(result: JDExtractionResult) -> JDExtractionResult:
    """执行完整后处理管线：
    1. 别名归一化
    2. 后缀清洗
    3. 黑名单剔除（行业/业务领域词，防 LLM 幻觉技能入图）
    4. 去重
    """
    # skills 与 requirements 共用同一清洗规则：别名 → 后缀清洗 → 黑名单剔除。
    # 额外剔除通用软素质词（吃苦耐劳/有责任心等，SOFT_SKILL_NOISE）：LLM 常把
    # 招聘软素质误抽为技术技能，此类词不入技能图谱（区别于 SOFT_SKILL_WHITELIST
    # 的 20 项岗位本体软技能，后者仍经 soft_skills 字段保留）。
    def _is_soft_noise(name: str) -> bool:
        return name in SOFT_SKILL_NOISE or any(
            n in name for n in SOFT_SKILL_NOISE if len(n) >= 4
        )

    def _clean(name: str) -> str:
        return canonical_skill_name(name)

    kept_skills: list[SkillExtracted] = []
    for s in result.skills:
        name = _clean(s.name)
        if not is_valid_skill_name(name) or _is_soft_noise(name):
            continue
        kept_skills.append(
            SkillExtracted(name=name, category=s.category, description=s.description)
        )
        # 白名单未命中但非噪音的技能进入灰名单验证区（新兴技术漏召回兜底，
        # 供观测池/置信度模型定向复核，白名单/停用词在 register_grey_skill 内豁免）
        register_grey_skill(name)
    result.skills = dedup_skills(kept_skills)

    # 软技能：仅保留岗位本体白名单（LLM 越界输出在此拦截，防非白名单词入岗位本体）
    seen_soft: set[str] = set()
    cleaned_soft: list[str] = []
    for s in result.soft_skills:
        name = canonical_skill_name(s).strip()
        if not name or name in seen_soft or name not in SOFT_SKILL_WHITELIST:
            continue
        seen_soft.add(name)
        cleaned_soft.append(name)
    result.soft_skills = cleaned_soft

    for tool in result.tools:
        # 工具名归一化：别名/大小写统一（防同工具分裂成多个图谱节点，
        # 如 Ansys/ANSYS、DeepSeek/Deepseek），与技能 normalize_skill 口径分离
        tool.name = normalize_tool_name(tool.name)

    # requirements 与 skills 使用同一清洗规则（避免非标准技能名入图），
    # 并按 (技能, 必要性) 去重，与 skills 去重口径一致
    cleaned_reqs = []
    seen: set[tuple[str, str]] = set()
    for req in result.requirements:
        name = _clean(req.skill_name)
        if not is_valid_skill_name(name) or _is_soft_noise(name):
            continue
        key = (name.lower(), req.necessity)
        if key in seen:
            continue
        seen.add(key)
        req.skill_name = name
        cleaned_reqs.append(req)
        # requirements 亦为 JD 技能来源：白名单外技能同步注册灰名单验证区
        register_grey_skill(name)
    result.requirements = cleaned_reqs

    return result
