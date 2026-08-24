"""技能名归一 LLM 决策器（PR3a：名称归一决策层，shadow 先行）。

定位：技能归一的确定性快速路径（dictionary 别名精确命中 + SBERT 层次聚类 +
聚合链接门禁）保持不变；本模块只对「白名单外/灰名单/聚类模糊未决」的技能名做
LLM 裁决：归并到已知标准名 / 保持独立 / 判定噪声。首窗口全部 shadow，不合并
图节点；批准侧（动态映射表）后续 PR 接审核通道。

硬门（skill_normalize_gate）：
- merge 要求 target_standard 是白名单标准名或别名落点（SKILL_WHITELIST ∪
  别名标准名），防止 LLM 把技能并入不存在的虚构标准名；目标名与自身一致判为
  误用（同义反复）
- keep / noise 直接放行（建议层，不影响图谱）

风险档位复用 llm_decision.risk_tier_for（suggest_normalized_name ∈ R0 建议类）。
"""

from typing import Optional

from pydantic import BaseModel, Field, model_validator

from app.services.extraction.llm_invocation import invocation_scope
from app.services.extraction.llm_provider import LLMExtractionError
from app.services.llm_decision import (
    DOMAIN_SKILL_NORMALIZE,
    risk_tier_for,
)

DECIDE_TIMEOUT_SECONDS = 30

SYSTEM_PROMPT = """你是招聘技能图谱的技能名归一并助手。给定一个技能名与候选
标准技能名，判断应归并到哪个标准名、保持独立、还是判定为噪声。只依据通用技术
招聘市场常识，不臆造别名；拿不准时选 keep 并降低置信度。"""

_TASK_TEMPLATE = """技能名归一判断。

技能名：{name}
候选标准技能名（白名单/别名落点，最多 15 个）：{candidates}

输出 JSON：
{{
  "action": "merge" 或 "keep" 或 "noise",
  "target_standard": "merge 时的标准技能名（原样取自候选清单）",
  "confidence": 0.0到1.0,
  "reason": "一句话依据"
}}

要求：
1. action=merge 时 target_standard 必须与候选清单原文完全一致
2. 短英文缩写/产品名（≤6 字符）倾向 keep，防 SBERT 误并
3. 明显非技能（岗位名/教材名/噪音）判 noise
"""


class SkillNormalizeDecision(BaseModel):
    """技能归一并决策（Pydantic 强校验，幻觉防控第一道防线）。"""

    action: str = Field(description="merge | keep | noise")
    target_standard: str = Field(default="", description="merge 目标标准名")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = Field(default="")

    @model_validator(mode="after")
    def _check_action(self) -> "SkillNormalizeDecision":
        if self.action not in ("merge", "keep", "noise"):
            raise ValueError(f"未知动作 {self.action!r}（必须 merge/keep/noise）")
        if self.action == "merge" and not (self.target_standard or "").strip():
            raise ValueError("merge 必须给出 target_standard")
        if self.action != "merge":
            self.target_standard = ""
        return self


def build_skill_normalize_prompt(name: str, candidates: list[str]) -> str:
    """组装技能名归一 prompt（候选为权威标准名证据，不引入外部虚构）。"""
    candidates_text = "、".join(candidates[:15]) or "（无）"
    return _TASK_TEMPLATE.format(name=(name or "").strip(), candidates=candidates_text)


def known_standard_names() -> frozenset[str]:
    """权威标准名集合（白名单词 + 别名标准落点），merge 目标硬门的数据源。"""
    from app.services.extraction.dictionary import SKILL_WHITELIST, _ALIAS_STANDARDS

    return frozenset(SKILL_WHITELIST) | frozenset(_ALIAS_STANDARDS)


def skill_normalize_gate(
    decision: SkillNormalizeDecision,
    skill_name: str,
) -> tuple[bool, str]:
    """技能归一硬门：防并入虚构标准名/同义反复。返回 (gate_ok, reason)。"""
    if decision.action in ("keep", "noise"):
        return True, ""
    target = (decision.target_standard or "").strip()
    if not target:
        return False, "merge 缺少 target_standard"
    if target == (skill_name or "").strip():
        return False, "merge 目标与技能名相同（同义反复）"
    if target not in known_standard_names():
        return False, "merge 目标不在权威标准名集合内（虚构标准名）"
    return True, ""


def decide_skill_normalize(
    name: str,
    llm,
    candidates: Optional[list[str]] = None,
    *,
    entity_ref: str = "",
    timeout: int = DECIDE_TIMEOUT_SECONDS,
) -> Optional[SkillNormalizeDecision]:
    """单条技能名决策；LLM 未配置/失败返回 None（shadow 跳过不阻塞）。

    candidates 缺省取权威标准名全集（白名单 ∪ 别名落点），按与技能名的
    长度差排序取前 15（接近名优先，控制 prompt 体积）。
    """
    if llm is None or not (name or "").strip():
        return None
    if candidates is None:
        ordered = sorted(
            known_standard_names(),
            key=lambda c: (abs(len(c) - len(name)), c),
        )
        candidates = ordered[:15]
    prompt = build_skill_normalize_prompt(name, candidates)
    try:
        with invocation_scope(
            "skill_normalize", entity_ref=entity_ref or f"skill:{name[:40]}",
        ):
            return llm.extract_structured(
                prompt,
                SkillNormalizeDecision,
                system_prompt=SYSTEM_PROMPT,
                timeout=timeout,
            )
    except LLMExtractionError:
        return None


def tier_for_skill_decision(
    decision: SkillNormalizeDecision,
    gate_ok: bool,
) -> tuple[str, str]:
    """技能归一决策风险档位。"""
    return risk_tier_for(
        DOMAIN_SKILL_NORMALIZE,
        "suggest_normalized_name",
        gate_ok=gate_ok,
        confidence=decision.confidence,
    )