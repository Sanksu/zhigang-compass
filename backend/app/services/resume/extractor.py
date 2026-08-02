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

        return self._filter_skills(result)

    @staticmethod
    def _filter_skills(result: ResumeExtractionResult) -> ResumeExtractionResult:
        """技能清洗：别名归一化 + 中文后缀清洗 + 黑名单剔除 + 去重。

        与 JD 抽取 post_process 同口径（normalize_skill + clean_skill_name +
        SKILL_STOPWORDS），保证候选技能名与图谱标准技能名一致，匹配时可命中。
        """
        from app.services.extraction.dictionary import SKILL_STOPWORDS, normalize_skill
        from app.services.extraction.post_processor import clean_skill_name

        seen: set[str] = set()
        cleaned = []
        for s in result.skills:
            name = clean_skill_name(normalize_skill(s.name)).strip()
            key = name.lower()
            if not name or name in SKILL_STOPWORDS or key in seen:
                continue
            seen.add(key)
            cleaned.append(s.model_copy(update={"name": name}))
        result.skills = cleaned
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
