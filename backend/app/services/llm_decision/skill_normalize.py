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
2. 同一技能的变体应 merge 到对应标准名——缩写/全称（JS→JavaScript）、
   大小写与拼写变体（react→React、golang→Go）、版本号（Python3→Python）、
   中英对应（c语言→C）；候选清单里的关联名优先考虑
3. 与自身相同的目标不是 merge（标准名输入直接 keep）；拿不准语义是否
   同一时 keep 并降低置信度；无关联缩写（≤6 字符）保持 keep 防 SBERT 误并
4. 明显非技能（岗位名/教材名/噪音）判 noise
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


def candidate_rank_key(name: str, candidate: str) -> tuple:
    """候选召回排序键（校准 r1）：词面关联优先于长度差。

    基线实证（docs/reviews/LLM驱动黄金集首跑基线_20260824.md）：纯长度差
    排序下 gold 标准名仅 2/60 进入候选前 15（react→React 长度差 0 仍被同长
    名挤出），merge accuracy 塌到 0.06 的决定性瓶颈是候选召回而非模型判断。
    分级：0=强关联（大小写不敏感相等/词面子串/去符号相等）；1=首字母相同
    （不区分大小写，覆盖缩写族 JS→JavaScript）；2=其余；同级按长度差+字典序。
    """
    import re as _re

    nl, cl = (name or "").lower(), (candidate or "").lower()

    def _strip(s: str) -> str:
        return _re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", s)

    # 缩写首字母匹配：JS = JavaScript 的大写首字母串（全大写短缩写专用）
    upper_initials = "".join(ch for ch in (candidate or "") if ch.isupper())
    abbr_match = (
        2 <= len(name or "") <= 6 and (name or "").isalpha() and (name or "").isupper()
        and upper_initials == (name or "")
    )
    # 中文后缀前缀：c语言→C（变体含 CJK 且以候选开头）
    cjk_prefix = any("\u4e00" <= ch <= "\u9fff" for ch in (name or "")) and cl and nl.startswith(cl)
    strong = (
        (nl == cl or nl in cl or cl in nl or _strip(nl) == _strip(cl))
        and min(len(nl), len(cl)) >= 2
    ) or abbr_match or cjk_prefix
    tier = 0 if strong else (1 if nl[:1] == cl[:1] and nl and cl else 2)
    return (tier, abs(len(cl) - len(nl)), candidate)


def decide_skill_normalize(
    name: str,
    llm,
    candidates: Optional[list[str]] = None,
    *,
    entity_ref: str = "",
    timeout: int = DECIDE_TIMEOUT_SECONDS,
) -> Optional[SkillNormalizeDecision]:
    """单条技能名决策；LLM 未配置/失败返回 None（shadow 跳过不阻塞）。

    candidates 缺省取权威标准名全集，按 candidate_rank_key（词面关联优先）
    排序取前 15——保证变体的强关联标准名（react→React、c#→C#）稳定入候选。
    """
    if llm is None or not (name or "").strip():
        return None
    if candidates is None:
        ordered = sorted(
            known_standard_names(),
            key=lambda c: candidate_rank_key(name, c),
        )
        candidates = ordered[:15]
        # 权威别名提示（校准 r3）：输入命中别名表时，其标准落点置顶入候选。
        # 与生产一致性对齐——别名/白名单是确定性快速路径，决策器是对齐后的
        # 一致确认而非独立猜测（r2 遗留：跨语言 full stack→全栈、llm→大语言
        # 模型等 40 例纯词面分级无法召回；别名表即权威对应）
        from app.services.extraction.dictionary_data import SKILL_ALIAS

        alias_target = SKILL_ALIAS.get(name) or SKILL_ALIAS.get(name.lower())
        if alias_target and alias_target not in candidates:
            candidates = [alias_target] + [c for c in candidates if c != alias_target]
            candidates = candidates[:15]
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