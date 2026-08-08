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
        concurrency: int = 1,
    ) -> list[JDExtractionResult]:
        """批量抽取多条 JD（设计文档 §6.5 批量抽取优化）。

        组批（batch_size 条数 + max_batch_chars 文本总长双封顶）→ 每批一次 LLM
        调用（省 N-1 次请求往返）→ 拆条返回；整批失败或返回条数错位时，该批
        降级为逐条 extract（含规则兜底）。顺序与输入严格一一对应。

        concurrency > 1 时多个批次经线程池并行：LLM 生成时间由输出 token 总量
        决定，单靠调大 batch_size 不线性提速（且受 max_tokens 截断约束），
        并发才是吞吐瓶颈；但并发受 provider 限流约束（429 由 LLMProviderChain
        指数退避兜底，退避期间整批失败会降级逐条，故并发不宜过高）。

        Args:
            jd_texts: 待抽取 JD 文本列表（已过滤过短文本）
            batch_size: 每批条数上限（建议 5~10）
            batch_timeout: 批量调用的独立超时（秒），缺省走 provider 异步默认
            max_batch_chars: 每批文本总长上限（字符），超限即切下一批
            concurrency: 并发批次上限（1 = 串行；>1 并行，建议 ≤ 4 防 429）
        """
        if not jd_texts:
            return []

        # LLM 未配置时直接逐条规则抽取（与 extract 单条降级口径一致）
        if self._llm is None:
            return [self.extract(text) for text in jd_texts]

        chunks = list(self._chunk_texts(jd_texts, batch_size, max_batch_chars))
        if concurrency <= 1:
            return [
                r for chunk in chunks for r in self._extract_chunk(chunk, batch_timeout)
            ]

        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            nested = list(executor.map(
                lambda c: self._extract_chunk(c, batch_timeout), chunks
            ))
        return [r for batch in nested for r in batch]

    def _extract_chunk(
        self, chunk: list[str], batch_timeout: Optional[int]
    ) -> list[JDExtractionResult]:
        """单批抽取：一次 LLM 调用；整批失败/错位降级逐条（含规则兜底）。

        独立方法供串行/并发两种路径复用（并发时各线程各自调用）。
        """
        system_prompt = SYSTEM_PROMPT + "\n\n" + FEW_SHOT_EXAMPLES
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
            return [post_process(r) for r in batch.results]
        except LLMExtractionError:
            # 整批失败（超时/校验/provider 全挂）→ 该批逐条抽取（单条有规则兜底）
            return [self.extract(text) for text in chunk]

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
            # P6：规则兜底仅文本扫描、无语义判断，不能断言"必备"——全标 nice，
            # 避免 LLM 不可用时 must_count 虚高把低频技能推成 must（聚合 _is_must
            # 依赖 must 标注占比，兜底数据不应污染判定）
            requirements=[
                REQUIRESRelation(skill_name=s["name"], necessity="nice")
                for s in found_skills
            ],
        )
