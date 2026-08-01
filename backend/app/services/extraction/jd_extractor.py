"""JD 实体抽取管线原型。

管线步骤（设计文档 5.2 节）：
1. LLM Few-Shot 抽取（5 条示例 + JSON Schema 强校验）
2. 词典后过滤（SKILL_ALIAS 扫描 JD，过滤 LLM 越界技能）
3. 中文后缀清洗 + 去重

LLM 调用走 LLMProviderChain（M3 参考实现）；未配置 api_key 时抛
LLMConfigurationError（LLMExtractionError 子类），降级规则抽取兜底。
"""

import json
import re
from typing import Optional

from app.services.extraction.schemas import JDExtractionResult
from app.services.extraction.llm_provider import LLMProviderChain, LLMExtractionError
from app.services.extraction.prompts import SYSTEM_PROMPT, TASK_TEMPLATE
from app.services.extraction.post_processor import post_process


class JDExtractor:
    """JD 实体抽取器。"""

    def __init__(self, llm: Optional[LLMProviderChain] = None):
        self._llm = llm or LLMProviderChain()

    def extract(self, jd_text: str) -> JDExtractionResult:
        """从 JD 文本中抽取结构化实体。

        LLM 抽取 → 词典过滤 → 后缀清洗 → 去重；LLM 不可用时规则抽取兜底。
        """
        if not jd_text or len(jd_text.strip()) < 10:
            return JDExtractionResult(position_name="")

        # LLM 抽取（无 api_key / 全 provider 失败时降级规则抽取）
        try:
            prompt = TASK_TEMPLATE.format(jd_text=jd_text)
            result = self._llm.extract_structured(prompt, JDExtractionResult)
        except LLMExtractionError:
            result = self._rule_based_extract(jd_text)

        return post_process(result)

    def _rule_based_extract(self, jd_text: str) -> JDExtractionResult:
        """基于规则的简单抽取（骨架阶段兜底）。"""
        import app.services.extraction.dictionary as d

        found_skills = []
        for skill in d.SKILL_WHITELIST:
            if skill.lower() in jd_text.lower():
                found_skills.append({"name": skill, "category": None, "description": None})

        # 岗位名称：取第一行或第一个标题
        lines = [ln.strip() for ln in jd_text.strip().split("\n") if ln.strip()]
        pos_name = ""
        for ln in lines[:5]:
            if len(ln) < 30 and not ln.startswith(("http", "#", "•", "-", "*")):
                pos_name = ln
                break

        from app.services.extraction.schemas import SkillExtracted, REQUIRESRelation

        return JDExtractionResult(
            position_name=pos_name,
            skills=[SkillExtracted(**s) for s in found_skills],
            requirements=[
                REQUIRESRelation(skill_name=s["name"], necessity="must")
                for s in found_skills
            ],
        )
