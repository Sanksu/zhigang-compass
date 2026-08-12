"""简历实体抽取器（LLM 抽取 + 规则兜底）。

流程（设计文档 §8.2/8.3）：脱敏文本 → LLM 结构化抽取（instructor 强校验，
幻觉防控第一道防线）→ LLM 不可用时规则抽取兜底 → 技能黑名单过滤
（与 JD 抽取 SKILL_STOPWORDS 同口径，防行业/业务词误入画像）。
"""

import logging
import re
from typing import Optional

from app.services.extraction.llm_provider import (
    LLMConfigurationError,
    LLMExtractionError,
    LLMProviderChain,
)
from app.services.resume.prompts import RESUME_SYSTEM_PROMPT, RESUME_TASK_TEMPLATE
from app.services.resume.schemas import ResumeExtractionResult, ResumeSkill

logger = logging.getLogger(__name__)

# 学历关键词按从高到低排列，规则兜底时取首个命中（即最高学历）
_EDUCATION_LEVELS = ("博士", "硕士", "本科", "大专")

# 年限抽取分两级（顺序重要）：
# 1. _EXPERIENCE_RE：优先匹配「经验 N 年」上下文，避免「2018年入职，5年经验」把年份当年限；
# 2. _YEARS_RE：兜底匹配「N 年」，但用负向前瞻排除 1900-2099 的年份数字（如「2018年-2022年」）。
_EXPERIENCE_RE = re.compile(
    r"经验[^\d年]{0,4}(\d+(?:\.\d+)?)\s*年|(\d+(?:\.\d+)?)\s*年[^\d]{0,4}经验"
)
_YEARS_RE = re.compile(r"(?<!\d)(?!19\d{2}|20\d{2})(\d+(?:\.\d+)?)\s*年")

# 简历文本熟练度关键词 → proficiency 三档（对齐 ResumeSkill enum：1 了解/2 熟悉/3 精通）。
# 与 JD 侧 _PROFICIENCY_KEYWORDS 词表同源（精通/深入/专家/资深、掌握/熟练、了解/入门/基础），
# 但档位语义不同（JD 档=岗位要求档次 初级/中级/高级，简历档=个人熟练度 1/2/3），
# 故独立定义防语义错位；"熟悉"在简历侧即"熟悉"档（2），不被映射到 JD 初级档。
_RESUME_PROFICIENCY_KEYWORDS: list[tuple[tuple[str, ...], int]] = [
    (("精通", "深入", "专家", "资深"), 3),
    (("掌握", "熟练", "熟悉", "独立"), 2),
    (("了解", "入门", "基础"), 1),
]


def _skill_proficiency(resume_text: str, skill_name: str) -> int:
    """从简历文本提取技能熟练度（无命中默认 2 熟悉，与 ResumeSkill 默认一致）。

    取技能名之前、最近一个句读分隔符（，。；、,\\n）到技能名之间的片段匹配关键词，
    避免相邻技能熟练度互相误捕（"精通 Python，熟悉 Java"中 Java 不应受"精通"影响）。
    """
    idx = resume_text.find(skill_name)
    if idx < 0:
        return 2
    seg_start = max(resume_text.rfind(c, 0, idx) for c in "，。；、,\n")
    window = resume_text[:idx] if seg_start < 0 else resume_text[seg_start + 1 : idx]
    for keywords, level in _RESUME_PROFICIENCY_KEYWORDS:
        if any(k in window for k in keywords):
            return level
    return 2


class ResumeExtractor:
    """简历实体抽取器。"""

    def __init__(self, llm: Optional[LLMProviderChain] = None):
        try:
            self._llm = llm or LLMProviderChain()
        except LLMConfigurationError:
            # LLM 未配置（configs/llm_providers.yaml 缺失）：与调用期 LLM 失败同语义，
            # 降级纯规则抽取（见类 docstring「LLM 不可用时规则抽取兜底」）
            self._llm = None

    def extract(self, resume_text: str) -> ResumeExtractionResult:
        """从（已脱敏）简历文本中抽取结构化实体。

        LLM 抽取 → 技能黑名单过滤；LLM 不可用时规则抽取兜底。
        """
        if not resume_text or len(resume_text.strip()) < 10:
            return ResumeExtractionResult()

        if self._llm is None:
            result = self._rule_based_extract(resume_text)
        else:
            try:
                prompt = RESUME_TASK_TEMPLATE.format(resume_text=resume_text)
                result = self._llm.extract_structured(
                    prompt, ResumeExtractionResult, system_prompt=RESUME_SYSTEM_PROMPT
                )
                # LLM 返回空对象（provider/instructor 层解析失败，实测 3 个样本稳定复现）：
                # 视同抽取失败，与 LLMExtractionError 同语义回退规则兜底。
                # 简历技能为空即视为失败——黄金集实证规则兜底对其 F1=1.0，回退为纯收益。
                if not result.skills and not result.soft_skills:
                    result = self._rule_based_extract(resume_text)
            except LLMExtractionError:
                result = self._rule_based_extract(resume_text)

        result = self._filter_skills(result)
        result = self._merge_soft_skills(result)
        return self._fill_cert_issuers(result)

    @staticmethod
    def _fill_cert_issuers(result: ResumeExtractionResult) -> ResumeExtractionResult:
        """证书 issuer 补全与规范化。

        1. LLM 已给出 issuer：查简写映射规范化为全称（如"软考"→全称），
           未收录则保留 LLM 原值（避免词表误配覆盖含上下文的 issuer）。
        2. LLM 未给出 issuer：用证书名映射表补全（issuer_for）。
        """
        from app.services.resume.cert_issuers import canonical_issuer, issuer_for

        total = len(result.certifications)
        if total == 0:
            return result
        filled = 0
        canonicalized = 0
        unmatched: list[str] = []
        for cert in result.certifications:
            if cert.issuer:
                canonical = canonical_issuer(cert.issuer)
                if canonical != cert.issuer:
                    cert.issuer = canonical
                    canonicalized += 1
            else:
                issuer = issuer_for(cert.name)
                if issuer:
                    cert.issuer = issuer
                    filled += 1
                else:
                    unmatched.append(cert.name)
        logger.info(
            "证书 issuer 处理：共 %d 个，词表补全 %d 个，简写规范化 %d 个，未命中 %d 个（%s）",
            total,
            filled,
            canonicalized,
            len(unmatched),
            ", ".join(unmatched[:10]) + ("…" if len(unmatched) > 10 else ""),
        )
        return result

    @staticmethod
    def _filter_skills(result: ResumeExtractionResult) -> ResumeExtractionResult:
        """技能清洗：别名归一化 + 中文后缀清洗 + 黑名单剔除 + 去重 + 未匹配标记。

        与 JD 抽取 post_process 同口径（normalize_skill + clean_skill_name +
        SKILL_STOPWORDS），保证候选技能名与图谱标准技能名一致，匹配时可命中。
        归一化后仍不在 SKILL_WHITELIST 的长尾技能原样保留并标记 unmapped=True，
        走人工确认（设计文档 8.4 节），不静默丢弃也不强并入标准实体。
        """
        from app.services.extraction.dictionary import (
            SKILL_STOPWORDS,
            SKILL_WHITELIST,
        )
        from app.services.extraction.post_processor import canonical_skill_name

        seen: set[str] = set()
        cleaned = []
        for s in result.skills:
            name = canonical_skill_name(s.name).strip()
            key = name.lower()
            if not name or name in SKILL_STOPWORDS or key in seen:
                continue
            seen.add(key)
            cleaned.append(s.model_copy(
                update={"name": name, "unmapped": name not in SKILL_WHITELIST}
            ))
        result.skills = cleaned
        return result

    @staticmethod
    def _merge_soft_skills(result: ResumeExtractionResult) -> ResumeExtractionResult:
        """将 LLM 推断的软技能并入技能列表，标记 low_confidence。

        软技能仅限岗位本体白名单（SOFT_SKILL_WHITELIST）；推断来源（项目角色/
        经历）置信度低，匹配时降权 ×0.5（设计文档 9.2 节）。与显式技能重名时
        保留显式技能（不降权），避免推断项覆盖文本直述的更强证据。
        """
        from app.services.extraction.dictionary import (
            SOFT_SKILL_WHITELIST,
        )
        from app.services.extraction.post_processor import canonical_skill_name

        existing = {s.name.lower() for s in result.skills}
        for name in result.soft_skills:
            cleaned = canonical_skill_name(name).strip()
            if not cleaned or cleaned not in SOFT_SKILL_WHITELIST:
                continue
            if cleaned.lower() in existing:
                continue
            existing.add(cleaned.lower())
            result.skills.append(ResumeSkill(name=cleaned, proficiency=2, low_confidence=True))
        return result

    def _rule_based_extract(self, resume_text: str) -> ResumeExtractionResult:
        """基于规则的简单抽取（LLM 不可用时的骨架兜底）。"""
        import app.services.extraction.dictionary as d

        text_low = resume_text.lower()
        found_names = set()
        for skill in d.SKILL_WHITELIST:
            if skill.lower() in text_low:
                found_names.add(skill)
        # 别名写法（TS/JS/k8s/Vue 等，真实简历与黄金集常见）：命中后归一为标准名，
        # 避免漏抽；标准名已命中时跳过（同一技能的别名与标准名只记一次）
        for alias, std in d.SKILL_ALIAS.items():
            if std not in found_names and alias.lower() in text_low:
                found_names.add(std)
        found = [
            ResumeSkill(name=name, proficiency=_skill_proficiency(resume_text, name))
            for name in found_names
        ]

        education_level = next(
            (level for level in _EDUCATION_LEVELS if level in resume_text), ""
        )

        total_years = 0.0
        m = _EXPERIENCE_RE.search(resume_text)
        if not m:
            m = _YEARS_RE.search(resume_text)
        if m:
            # 两个捕获组分别对应「经验 N 年」与「N 年经验」两种写法
            total_years = float(m.group(1) or m.group(2))

        return ResumeExtractionResult(
            skills=found,
            education_level=education_level,
            total_years=total_years,
        )
