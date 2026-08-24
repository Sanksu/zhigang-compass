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
3. 中文标题保持中文；英文标题保持英文；中英混合按主语言归一
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