"""JD 实体抽取管线原型。

管线步骤（设计文档 5.2 节）：
1. LLM Few-Shot 抽取（5 条示例 + JSON Schema 强校验）
2. 词典后过滤（SKILL_ALIAS 扫描 JD，过滤 LLM 越界技能）
3. 中文后缀清洗 + 去重

LLM 调用走 LLMProviderChain（M3 参考实现）；未配置 api_key 时抛
LLMConfigurationError（LLMExtractionError 子类），降级规则抽取兜底。
"""

from typing import Optional

from app.services.extraction.schemas import (
    JDExtractionBatch,
    JDExtractionResult,
)
from app.services.extraction.llm_provider import (
    LLMConfigurationError,
    LLMExtractionError,
    LLMProviderChain,
)
from app.services.extraction.prompts import (
    BATCH_TASK_TEMPLATE,
    FEW_SHOT_EXAMPLES,
    SYSTEM_PROMPT,
    TASK_TEMPLATE,
)
from app.services.extraction.post_processor import post_process


class JDExtractor:
    """JD 实体抽取器。"""

    def __init__(self, llm: Optional[LLMProviderChain] = None):
        try:
            self._llm = llm or LLMProviderChain()
        except LLMConfigurationError:
            # LLM 未配置（configs/llm_providers.yaml 缺失）：与调用期 LLM 失败同语义，
            # 降级纯规则抽取（见类 docstring「LLM 不可用时规则抽取兜底」）
            self._llm = None

    def extract(self, jd_text: str) -> JDExtractionResult:
        """从 JD 文本中抽取结构化实体。

        LLM 抽取 → 词典过滤 → 后缀清洗 → 去重；LLM 不可用时规则抽取兜底。
        """
        if not jd_text or len(jd_text.strip()) < 10:
            return JDExtractionResult(position_name="")

        if self._llm is None:
            result = self._rule_based_extract(jd_text)
        else:
            # LLM 抽取（无 api_key / 全 provider 失败时降级规则抽取）
            try:
                prompt = TASK_TEMPLATE.format(jd_text=jd_text)
                # 分层 Prompt：system 角色（SYSTEM_PROMPT）+ Few-Shot + 任务输入（§6.2）
                system_prompt = SYSTEM_PROMPT + "\n\n" + FEW_SHOT_EXAMPLES
                result = self._llm.extract_structured(
                    prompt, JDExtractionResult, system_prompt=system_prompt
                )
            except LLMExtractionError:
                result = self._rule_based_extract(jd_text)

        return post_process(result)

    def extract_batch(
        self,
        jd_texts: list[str],
        batch_size: int = 5,
        batch_timeout: Optional[int] = None,
        max_batch_chars: Optional[int] = None,
    ) -> list[JDExtractionResult]:
        """批量抽取多条 JD（设计文档 §6.5 批量抽取优化）。

        组批（batch_size 条数 + max_batch_chars 文本总长双封顶）→ 每批一次 LLM
        调用（省 N-1 次请求往返）→ 拆条返回；整批失败或返回条数错位时，该批
        降级为逐条 extract（含规则兜底）。顺序与输入严格一一对应。

        Args:
            jd_texts: 待抽取 JD 文本列表（已过滤过短文本）
            batch_size: 每批条数上限（建议 5~10）
            batch_timeout: 批量调用的独立超时（秒），缺省走 provider 异步默认
            max_batch_chars: 每批文本总长上限（字符），超限即切下一批
        """
        if not jd_texts:
            return []

        # LLM 未配置时直接逐条规则抽取（与 extract 单条降级口径一致）
        if self._llm is None:
            return [self.extract(text) for text in jd_texts]

        system_prompt = SYSTEM_PROMPT + "\n\n" + FEW_SHOT_EXAMPLES
        results: list[JDExtractionResult] = []
        for chunk in self._chunk_texts(jd_texts, batch_size, max_batch_chars):
            try:
                prompt = BATCH_TASK_TEMPLATE.format(
                    jd_count=len(chunk),
                    jd_texts="\n---\n".join(f"JD文本{i + 1}: {t}" for i, t in enumerate(chunk)),
                )
                batch = self._llm.extract_structured(
                    prompt, JDExtractionBatch,
                    system_prompt=system_prompt, timeout=batch_timeout,
                )
                # 错位防护：LLM 返回条数与输入不一致时不可直接拆条，降级逐条
                if len(batch.results) != len(chunk):
                    raise LLMExtractionError(
                        f"批量返回 {len(batch.results)} 条 ≠ 输入 {len(chunk)} 条，降级逐条"
                    )
                results.extend(post_process(r) for r in batch.results)
            except LLMExtractionError:
                # 整批失败（超时/校验/provider 全挂）→ 该批逐条抽取（单条有规则兜底）
                for text in chunk:
                    results.append(self.extract(text))
        return results

    @staticmethod
    def _chunk_texts(
        texts: list[str],
        batch_size: int,
        max_batch_chars: Optional[int],
    ) -> list[list[str]]:
        """组批：batch_size 条数封顶 + max_batch_chars 文本总长封顶（双约束）。

        JD 正文长短不一（国际源完整 JSON 长、国内源摘要短），纯条数封顶可能
        撑爆单批上下文/超时；按文本总长二次封顶保证每批成本稳定。
        """
        chunks: list[list[str]] = []
        current: list[str] = []
        current_chars = 0
        for text in texts:
            if current and (
                len(current) >= batch_size
                or (max_batch_chars is not None and current_chars + len(text) > max_batch_chars)
            ):
                chunks.append(current)
                current = []
                current_chars = 0
            current.append(text)
            current_chars += len(text)
        if current:
            chunks.append(current)
        return chunks

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
