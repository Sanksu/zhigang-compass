"""岗位名归一 LLM 决策器（PR3a：名称归一决策层，shadow/proposal 风控先行）。

定位：岗位名归一的事实源仍以规则词典快速路径（dictionary.normalize_position_name，
岗位关键词族/白名单/别名）为准；本模块只对「规则无法稳定裁决」的开放世界输入
做 LLM 语义判断，输出标准名建议。首窗口全部 shadow（只落 llm_decision_records
决策记录，status=shadow），不自动重命名/合并图谱节点。

硬门（position_name_gate）：
- keep_original=True 视为确认原样，直接放行（仅建议层）
- canonical 空/过短(<2)/过长(>40) → block（防幻觉长名/空名）
- 断言新岗位（is_new=True）或与原始标题一致 → 放行
- 非新岗位但 canonical 不在本次候选岗位名内 → block（证据不足的自创名）

风险档位复用 llm_decision.risk_tier_for（suggest_normalized_name ∈ R0 建议类，
验收通过后仅该档可灰度自动；R2 由下游人工通道接手）。
"""

from typing import Optional

from pydantic import BaseModel, Field, model_validator

from app.services.extraction.llm_invocation import invocation_scope
from app.services.extraction.llm_provider import LLMExtractionError
from app.services.llm_decision import (
    DOMAIN_POSITION_NORMALIZE,
    risk_tier_for,
)

# 单条决策超时（s）：批量影子任务非同步路由，30s/provider 链契约
DECIDE_TIMEOUT_SECONDS = 30

# 批量独立超时（s）：设计文档 §6.5「批量走独立 batch_timeout（60~90s/批）」
# ——此前 30×batch_size=600s/次、3 provider 链最坏 1800s 偏离契约
BATCH_DECIDE_TIMEOUT_SECONDS = 90

SYSTEM_PROMPT = """你是招聘领域岗位名归一助手。给定一个原始岗位标题及其抽取出的
技能、来源与候选标准岗位名，判断该标题应归为哪个标准岗位名。只依据通用招聘市场
常识判断，不臆造；拿不准时选 keep_original 并降低置信度。"""

_TASK_TEMPLATE = """岗位名归一判断。

原始标题：{title}
来源：{source}
JD 抽取技能：{skills}
候选标准岗位名（已存在图谱，最多 20 个）：{candidates}

输出 JSON：
{{
  "canonical_name": "标准岗位名（与原始标题同语言；keep_original 时可为原始标题）",
  "is_new": true/false,
  "keep_original": true/false,
  "confidence": 0.0到1.0,
  "reason": "一句话依据"
}}

要求：
1. canonical_name 必须来自候选清单或原始标题本身的合理整理，不得凭空创造新名
2. is_new=true 仅在"该岗位语义不在任何候选中且确实构成新岗位"时使用
3. 中文标题保持中文；英文标题优先用权威标准名候选（如 ai engineer→算法工程师）
4. **原岗位名含"实习/见习"要归到正式岗位**（如"前端开发实习生"→"前端开发工程师"），
   不得 keep 为实习生原样；这是招聘形态非正式岗位族
5. **职能岗（架构师/算法工程师/研究员/科学家/分析师）是独立职能**，勿因技术栈
   拆分到具体开发方向（"系统架构师"应归"架构师"类，而非"前端/后端开发工程师"）
6. 中英混合按主语言归一；拿不准时 keep_original 并降置信度
"""


class PositionNameDecision(BaseModel):
    """岗位名归一决策（Pydantic 强校验，幻觉防控第一道防线）。"""

    canonical_name: str = Field(default="", description="建议标准岗位名")
    is_new: bool = Field(default=False, description="是否新岗位")
    keep_original: bool = Field(default=False, description="保持原样不改名")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = Field(default="")

    @model_validator(mode="after")
    def _check_consistency(self) -> "PositionNameDecision":
        if self.keep_original:
            self.canonical_name = self.canonical_name or ""
            return self
        if not (self.canonical_name or "").strip():
            raise ValueError("canonical_name 不能为空（keep_original=false 时）")
        return self


def build_position_name_prompt(
    title: str,
    skills: list[str],
    source: str,
    candidates: list[str],
) -> str:
    """组装岗位名归一 prompt（输入均为证据摘要，不包含敏感原文以外的字段）。"""
    skills_text = "、".join(skills[:30]) or "（空）"
    candidates_text = "、".join(candidates[:20]) or "（无）"
    return _TASK_TEMPLATE.format(
        title=(title or "").strip(),
        source=(source or "").strip() or "unknown",
        skills=skills_text,
        candidates=candidates_text,
    )


# 批量决策条数上限（05-25 提速：deepseek 100M 上下文一次打包多条，均摊往返）。
# 实测 N=20 时 0.9~1.0s/条（vs 单条 16s），且质量与单条相当（85% vs 80%）。
POSITION_BATCH_SIZE = 20


class PositionNameBatch(BaseModel):
    """批量岗位名归一容器（instructor 对 list[BaseModel] 用包装模型更稳）。

    一次 LLM 调用决策多条岗位名：`results` 顺序与输入严格一一对应，调用方据此
    拆条。条数不符即判定错位风险，降级逐条（对齐 JDExtractionBatch 语义）。
    """

    results: list[PositionNameDecision] = Field(
        default_factory=list,
        description="批量归一决策，数组第 i 个元素对应输入第 i 个岗位",
    )


# 批量模板（05-25 提速 + prompt 强化）：jobs 每行含 title/source/skills/candidates，
# LLM 逐条输出 canonical/is_new/keep_original；强化规则吸收 33 分歧点：
#   a) 实习生岗（标题含"实习/见习"）归正式岗位名（前端开发实习生→前端开发工程师），
#      不得 keep 为实习生原样（生产 normalize_position_name 对"实习"返回空不入图）
#   b) 职能岗（架构师/算法工程师/研究员/科学家）是独立职能，勿因技术栈拆分到
#      具体开发方向（如"系统架构师"≠"前端开发工程师"，应归"架构师"类）
#   c) 英文标题优先归 _EN_POSITION_MAP 权威中文映射（ai engineer→算法工程师），
#      非用户输入语言，而是图谱标准名语言
_BATCH_TASK_TEMPLATE = """批量岗位名归一判断（一次 {count} 条，逐条输出，顺序严格对应）。

{items}

输出 JSON：
{{
  "results": [
    {{
      "canonical_name": "标准岗位名（与图谱标准名同语言；keep_original 时可为原始标题）",
      "is_new": true/false,
      "keep_original": true/false,
      "confidence": 0.0到1.0,
      "reason": "一句话依据"
    }}
  ]
}}

要求（逐条）：
1. canonical_name 必须来自该题候选清单或原始标题本身的合理整理，不得凭空创造新名
2. is_new=true 仅在"该岗位语义不在任何候选中且确实构成新岗位"时使用
3. 中文标题保持中文；英文标题优先用权威标准名候选（如 ai engineer→算法工程师）
4. **原岗位名含"实习/见习"要归到正式岗位**（如"前端开发实习生"→"前端开发工程师"），
   不得 keep 为实习生原样；这是招聘形态非正式岗位族
5. **职能岗（架构师/算法工程师/研究员/科学家/分析师）是独立职能**，勿因技术栈
   拆分到具体开发方向（"系统架构师"应归"架构师"类，而非"前端/后端开发工程师"）
6. 中英混合按主语言归一；拿不准时 keep_original 并降置信度
"""


def build_position_name_batch_prompt(
    titles: list[str],
    sources: list[str],
    skills_list: list[list[str]],
    candidates_list: list[list[str]],
) -> str:
    """组装批量岗位名归一 prompt（每行一道，含标题/来源/技能/候选）。"""
    items: list[str] = []
    for i, title in enumerate(titles):
        skills = "、".join((skills_list[i] if i < len(skills_list) else [])[:30]) or "（空）"
        cands = "、".join((candidates_list[i] if i < len(candidates_list) else [])[:20]) or "（无）"
        items.append(
            f"[{i}] 原始标题：{title.strip()}\n"
            f"    来源：{sources[i].strip() if i < len(sources) else '' or 'unknown'}\n"
            f"    JD 抽取技能：{skills}\n"
            f"    候选标准岗位名：{cands}"
        )
    return _BATCH_TASK_TEMPLATE.format(count=len(titles), items="\n\n".join(items))


def decide_position_name_batch(
    titles: list[str],
    sources: list[str],
    skills_list: list[list[str]],
    candidates_list: list[list[str]],
    llm,
    *,
    batch_size: int = POSITION_BATCH_SIZE,
    timeout: int = 0,
) -> list[Optional[PositionNameDecision]]:
    """批量岗位名归一决策（一次 LLM 调用多条，均摊往返提速）。

    实测 N=20 时 ~1s/条（vs 单条 16s，16×），质量相当。LLM 未配置/失败返回
    [None]*count（shadow 跳过不阻塞）。条数与输入不符（instructor 包装模型
    偶发错位）时降级逐条单决策。
    """
    if timeout <= 0:
        timeout = BATCH_DECIDE_TIMEOUT_SECONDS
    count = len(titles)
    if llm is None or count == 0:
        return [None] * count
    # 分批：每批 batch_size 条（条数统一，防超长 prompt）
    results: list[Optional[PositionNameDecision]] = []
    for start in range(0, count, batch_size):
        chunk = titles[start:start + batch_size]
        s_chunk = sources[start:start + batch_size]
        sk_chunk = skills_list[start:start + batch_size]
        c_chunk = candidates_list[start:start + batch_size]
        prompt = build_position_name_batch_prompt(chunk, s_chunk, sk_chunk, c_chunk)
        try:
            with invocation_scope(
                "position_normalize_batch", entity_ref=f"jd_batch:{start}:{len(chunk)}",
            ):
                batch = llm.extract_structured(
                    prompt, PositionNameBatch,
                    system_prompt=SYSTEM_PROMPT, timeout=timeout,
                )
            got = list(batch.results)
            if len(got) != len(chunk):
                # 错位：降级逐条
                for i in range(start, start + len(chunk)):
                    results.append(
                        decide_position_name(titles[i], skills_list[i] if i < len(skills_list) else [],
                                              sources[i] if i < len(sources) else "", candidates_list[i] if i < len(candidates_list) else [], llm)
                    )
            else:
                results.extend(got)
        except LLMExtractionError:
            # 单批失败：该批降级逐条（宁可慢不可丢）
            for i in range(start, start + len(chunk)):
                results.append(
                    decide_position_name(titles[i], skills_list[i] if i < len(skills_list) else [],
                                          sources[i] if i < len(sources) else "", candidates_list[i] if i < len(candidates_list) else [], llm)
                )
    return results


def position_name_gate(
    decision: PositionNameDecision,
    raw_title: str,
    candidates: list[str],
) -> tuple[bool, str]:
    """岗位名决策硬门：防幻觉长名/空名/自创名。返回 (gate_ok, reason)。"""
    if decision.keep_original:
        return True, ""
    canonical = (decision.canonical_name or "").strip()
    if not canonical:
        return False, "canonical_name 为空"
    if len(canonical) < 2 or len(canonical) > 40:
        return False, f"canonical_name 长度越界（{len(canonical)}）"
    if decision.is_new or canonical == (raw_title or "").strip():
        return True, ""
    if canonical in set(candidates):
        return True, ""
    return False, "非新岗位但标准名不在候选清单内（证据不足的自创名）"


def decide_position_name(
    title: str,
    skills: list[str],
    source: str,
    candidates: list[str],
    llm,
    *,
    entity_ref: str = "",
    timeout: int = DECIDE_TIMEOUT_SECONDS,
) -> Optional[PositionNameDecision]:
    """单条岗位名决策；LLM 未配置/失败返回 None（shadow 跳过不阻塞）。"""
    if llm is None or not (title or "").strip():
        return None
    prompt = build_position_name_prompt(title, skills, source, candidates)
    try:
        with invocation_scope(
            "position_normalize", entity_ref=entity_ref or f"jd:{title[:40]}",
        ):
            return llm.extract_structured(
                prompt,
                PositionNameDecision,
                system_prompt=SYSTEM_PROMPT,
                timeout=timeout,
            )
    except LLMExtractionError:
        return None


class PositionCandidateRecaller:
    """岗位名候选召回器：按标题语义余弦 Top-K（池向量构建时一次编码）。

    回流自实验场 zhigang-llm-driven（e0d53ab）：110 条岗位黄金集上
    SBERT Top-8 召回 0.927、词面谓词 0.10——与标题无关的全局热门截断
    对长尾标题召回趋近于 0。模型（设计文档 9.3 指定 SBERT）不可用时
    降级池前缀（即频次序），shadow 不阻塞。
    """

    def __init__(self, pool: list[str], k: int = 20):
        self._pool = [p.strip() for p in pool if p and p.strip()]
        self._k = k
        self._matrix = None
        self.mode = "pool-prefix"
        if len(self._pool) <= k:
            self.mode = "pool-full"
            return
        try:
            import numpy as np

            from app.services.matching.semantic import SkillEmbedder

            embedder = SkillEmbedder.get()
            matrix = np.asarray(
                [embedder.embed(p) for p in self._pool], dtype=np.float32,
            )
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            self._matrix = matrix / np.clip(norms, 1e-12, None)
            self.mode = "embedding"
        except Exception:
            self._matrix = None  # 模型未下载/依赖缺失：降级频次前缀

    def recall(self, title: str) -> list[str]:
        """标题 → 候选 Top-K；embedding 不可用或单条失败回退池前缀。

        08-25 补 pure_en 候选召回（③）：英文标题命中 _EN_POSITION_MAP 权威映射时，
        其中文目标（如 'ai engineer'→'算法工程师'）置顶并入候选——词面/语义召回对
        英文长尾标题覆盖差，权威映射是确定性事实源。映射目标已是规范化中文岗位名
        （图谱节点也用该名），故直接置顶不冲突。
        """
        title = (title or "").strip()
        # pure_en 权威映射置顶：英文标题 → 权威中文岗位名
        from app.services.extraction.dictionary import _EN_POSITION_MAP

        en_target = _EN_POSITION_MAP.get(title.lower())
        base = self._pool[: self._k] if (self._matrix is None or len(self._pool) <= self._k) else self._k_pool(title)
        if en_target:
            # 权威目标置顶，去重后保留 Top-K
            dedup = [en_target] + [p for p in base if p != en_target]
            return dedup[: self._k]
        return base

    def _k_pool(self, title: str) -> list[str]:
        """embedding 可用时的 Top-K 召回（原 recall 主体）。"""
        try:
            import numpy as np

            from app.services.matching.semantic import SkillEmbedder

            q = np.asarray(
                SkillEmbedder.get().embed((title or "").strip()), dtype=np.float32,
            )
            q = q / max(float(np.linalg.norm(q)), 1e-12)
            order = np.argsort(-(self._matrix @ q))
            return [self._pool[i] for i in order[: self._k]]
        except Exception:
            return self._pool[: self._k]


def tier_for_position_decision(
    decision: PositionNameDecision,
    gate_ok: bool,
) -> tuple[str, str]:
    """岗位名决策风险档位（R0 建议类 / blocked 硬门失败）。"""
    return risk_tier_for(
        DOMAIN_POSITION_NORMALIZE,
        "suggest_normalized_name",
        gate_ok=gate_ok,
        confidence=decision.confidence,
    )