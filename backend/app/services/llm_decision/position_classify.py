"""岗位分类 LLM 决策器（PR4：岗位域/职能分类决策层，shadow 先行）。

定位：岗位分类的权威枚举以调用方提供的「已知分类清单」为载体（职业库
Occupation.category 去重值 / 岗位职能域枚举）。LLM 只在该清单内做选择——
硬门要求 category 必须命中清单原文，防止自创分类；首窗口仅决策记录
（shadow），不写权威字段。

风险档位复用 llm_decision.risk_tier_for（suggest_category ∈ R0 建议类）。
"""

from typing import Optional

from pydantic import BaseModel, Field, model_validator

from app.services.extraction.llm_invocation import invocation_scope
from app.services.extraction.llm_provider import LLMExtractionError
from app.services.llm_decision import (
    DOMAIN_POSITION_CLASSIFY,
    risk_tier_for,
)

DECIDE_TIMEOUT_SECONDS = 30

SYSTEM_PROMPT = """你是招聘岗位分类助手。给定岗位名与其抽取技能，从提供的
分类清单中选择唯一最合适的分类。只依据通用招聘市场常识判断，不臆造；
不确定时给出较低置信度，不要把清单外的分类写进输出。"""

_TASK_TEMPLATE = """岗位分类判断。

岗位名：{position_name}
抽取技能：{skills}
分类清单（必须从中选一，原样输出）：{categories}

输出 JSON：
{{
  "category": "清单中的某一项",
  "confidence": 0.0到1.0,
  "reason": "一句话依据"
}}

要求：
1. category 必须与清单原文完全一致（含标点/斜杠），不得自创
2. 拿不准时选最接近的大类并降低 confidence
"""


class PositionClassifyDecision(BaseModel):
    """岗位分类决策（Pydantic 强校验，幻觉防控第一道防线）。"""

    category: str = Field(default="", description="分类名，必须来自已知清单")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = Field(default="")

    @model_validator(mode="after")
    def _check_category(self) -> "PositionClassifyDecision":
        if not (self.category or "").strip():
            raise ValueError("category 不能为空")
        return self


def build_position_classify_prompt(
    position_name: str,
    skills: list[str],
    categories: list[str],
) -> str:
    """组装岗位分类 prompt（categories 为权威枚举证据，最多 40 项）。"""
    return _TASK_TEMPLATE.format(
        position_name=(position_name or "").strip(),
        skills="、".join(skills[:30]) or "（空）",
        categories="、".join(categories[:40]),
    )


def position_classify_gate(
    decision: PositionClassifyDecision,
    known_categories: list[str],
) -> tuple[bool, str]:
    """岗位分类硬门：category 必须命中已知清单原文（防自创分类）。"""
    category = (decision.category or "").strip()
    if not category:
        return False, "category 为空"
    if category not in set(known_categories):
        return False, "category 不在清单内（自创分类）"
    return True, ""


def decide_position_classify(
    position_name: str,
    skills: list[str],
    categories: list[str],
    llm,
    *,
    entity_ref: str = "",
    timeout: int = DECIDE_TIMEOUT_SECONDS,
) -> Optional[PositionClassifyDecision]:
    """单条岗位分类决策；LLM 未配置/失败返回 None（shadow 跳过不阻塞）。"""
    if llm is None or not (position_name or "").strip() or not categories:
        return None
    prompt = build_position_classify_prompt(position_name, skills, categories)
    try:
        with invocation_scope(
            "position_classify", entity_ref=entity_ref or f"position:{position_name[:40]}",
        ):
            return llm.extract_structured(
                prompt,
                PositionClassifyDecision,
                system_prompt=SYSTEM_PROMPT,
                timeout=timeout,
            )
    except LLMExtractionError:
        return None


def tier_for_position_classify(
    decision: PositionClassifyDecision,
    gate_ok: bool,
) -> tuple[str, str]:
    """岗位分类决策风险档位。"""
    return risk_tier_for(
        DOMAIN_POSITION_CLASSIFY,
        "suggest_category",
        gate_ok=gate_ok,
        confidence=decision.confidence,
    )