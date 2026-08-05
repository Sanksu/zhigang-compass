"""简历实体抽取器（LLM 抽取 + 规则兜底）。

流程（设计文档 §8.2/8.3）：脱敏文本 → LLM 结构化抽取（instructor 强校验，
幻觉防控第一道防线）→ LLM 不可用时规则抽取兜底 → 技能黑名单过滤
（与 JD 抽取 SKILL_STOPWORDS 同口径，防行业/业务词误入画像）。
"""

import re
from typing import Optional

from app.services.extraction.llm_provider import LLMProviderChain, LLMExtractionError
from app.services.resume.prompts import RESUME_SYSTEM_PROMPT, RESUME_TASK_TEMPLATE
from app.services.resume.schemas import ResumeExtractionResult, ResumeSkill

# 学历关键词按从高到低排列，规则兜底时取首个命中（即最高学历）
_EDUCATION_LEVELS = ("博士", "硕士", "本科", "大专")
_YEARS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*年")


class ResumeExtractor:
    """简历实体抽取器。"""

    def __init__(self, llm: Optional[LLMProviderChain] = None):
        self._llm = llm or LLMProviderChain()

    def extract(self, resume_text: str) -> ResumeExtractionResult:
        """从（已脱敏）简历文本中抽取结构化实体。

        LLM 抽取 → 技能黑名单过滤；LLM 不可用时规则抽取兜底。
        """
        if not resume_text or len(resume_text.strip()) < 10:
            return ResumeExtractionResult()

        try:
            prompt = RESUME_TASK_TEMPLATE.format(resume_text=resume_text)
            result = self._llm.extract_structured(
                prompt, ResumeExtractionResult, system_prompt=RESUME_SYSTEM_PROMPT
            )
        except LLMExtractionError:
            result = self._rule_based_extract(resume_text)

        result = self._filter_skills(result)
        return self._merge_soft_skills(result)

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
            normalize_skill,
        )
        from app.services.extraction.post_processor import clean_skill_name

        seen: set[str] = set()
        cleaned = []
        for s in result.skills:
            name = clean_skill_name(normalize_skill(s.name)).strip()
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
            normalize_skill,
        )
        from app.services.extraction.post_processor import clean_skill_name

        existing = {s.name.lower() for s in result.skills}
        for name in result.soft_skills:
            cleaned = clean_skill_name(normalize_skill(name)).strip()
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
        found = []
        for skill in d.SKILL_WHITELIST:
            if skill.lower() in text_low:
                found.append(ResumeSkill(name=skill, proficiency=2))

        education_level = next(
            (level for level in _EDUCATION_LEVELS if level in resume_text), ""
        )

        total_years = 0.0
        m = _YEARS_RE.search(resume_text)
        if m:
            total_years = float(m.group(1))

        return ResumeExtractionResult(
            skills=found,
            education_level=education_level,
            total_years=total_years,
        )
