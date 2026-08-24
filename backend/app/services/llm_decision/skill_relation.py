"""技能关系 LLM 决策器（PR6：关系类型/方向判定 + 图不变量硬门，proposal 先行）。

定位：现有 YAML 关系作为稳定种子基线不变（skill_relations.sync_skill_relations
继续负责种子落图）。本模块对「候选关系对」做 LLM 语义判定：
`PREREQUISITE_OF | BELONGS_TO | ALTERNATIVE_OF | NONE` + 方向/置信度/理由；
首窗口全部只落 proposal（llm_decision_records status=proposal，risk_tier=R2），
审核通过前不写入图谱。

硬门（skill_relation_gate）——防无效/危险关系：
- relation=NONE → 放行（无写入语义）
- 目标非空、与源不同（无自指）、目标在已知技能名集合内（防虚构节点）
- direction 与关系类型匹配：BELONGS_TO/PREREQUISITE_OF 仅 a_to_b
  （源→目标，子→父 / 先修→目标）；ALTERNATIVE_OF 为对称语义
运行侧额外不变量（写入前执行）：
- 先修无环（prerequisite_cycle_would_create：沿既有 PREREQUISITE_OF 入边
  展开，新增 (source→target) 若可达 source 即成环）
- 替代对称去重（与既有 ALTERNATIVE_OF 冲突视为重复而非新关系）

红线：prompt 与方向语义属算法核心，变更须张恺天 review。
"""

from typing import Optional

from pydantic import BaseModel, Field, model_validator

from app.services.extraction.llm_invocation import invocation_scope
from app.services.extraction.llm_provider import LLMExtractionError
from app.services.llm_decision import (
    DOMAIN_SKILL_RELATION,
    risk_tier_for,
)

DECIDE_TIMEOUT_SECONDS = 30

REL_PREREQUISITE = "PREREQUISITE_OF"
REL_BELONGS = "BELONGS_TO"
REL_ALTERNATIVE = "ALTERNATIVE_OF"
REL_NONE = "NONE"
REL_TYPES = frozenset({REL_PREREQUISITE, REL_BELONGS, REL_ALTERNATIVE, REL_NONE})

SYSTEM_PROMPT = """你是招聘技能图谱的关系判定助手。给定一对技能名与它们在岗位
JD 中的共现证据，判定两者关系类型与方向。只依据通用技术常识与给定证据，不臆造；
拿不准时判 NONE 并降低置信度。"""

_TASK_TEMPLATE = """技能关系判定。

源技能：{source}
目标技能：{target}
共现证据（岗位 → 出现次数）：{evidence}

输出 JSON：
{{
  "relation": "PREREQUISITE_OF | BELONGS_TO | ALTERNATIVE_OF | NONE",
  "direction": "a_to_b | symmetric",
  "confidence": 0.0到1.0,
  "reason": "一句话依据"
}}

语义约定（判定判据，先修/父子易混须严格区分）：
- PREREQUISITE_OF：源是目标的前置先修——学目标之前通常需先掌握源
  （语言→其上构建的框架/平台、数学基础→算法领域）。技术栈依赖常识即可
  判定，不要因缺少共现证据而保守答 NONE；方向恒为 a_to_b（先修→目标）
- BELONGS_TO：源从属于目标（子领域/子技术→父领域，如某框架特性→框架
  本身）；方向恒为 a_to_b（子→父）
- ALTERNATIVE_OF：两者解决同类问题、岗位要求中可互相替代；方向 symmetric
- NONE：以上三者都不成立时才使用；拿不准语义关系时优先 NONE 并降置信度
- 只选最有把握的一种关系；多义时优先 BELONGS_TO → NONE
"""

# r3 判据版本（20260824 已回退）：长条文「区分自问+判别示例」使 BELONGS_TO
# 误判先修 15/26→22/26、先修命中 34→30（precision 0.6211→0.5053），负收益。
# 回退至 r2 措辞（0.6211 状态）；r4 改用「每型一个正例定义」精简句式重试。


class SkillRelationDecision(BaseModel):
    """技能关系决策（Pydantic 强校验，幻觉防控第一道防线）。"""

    relation: str = Field(description="PREREQUISITE_OF | BELONGS_TO | ALTERNATIVE_OF | NONE")
    direction: str = Field(default="a_to_b", description="a_to_b | symmetric")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = Field(default="")

    @model_validator(mode="after")
    def _check_consistency(self) -> "SkillRelationDecision":
        if self.relation not in REL_TYPES:
            raise ValueError(f"未知关系类型 {self.relation!r}")
        if self.direction not in ("a_to_b", "symmetric"):
            raise ValueError(f"未知方向 {self.direction!r}")
        if self.relation == REL_NONE:
            self.direction = "a_to_b"
        if self.relation in (REL_PREREQUISITE, REL_BELONGS):
            self.direction = "a_to_b"  # 强制单向（先修/父子语义）
        return self


def build_skill_relation_prompt(
    source: str,
    target: str,
    evidence: list[dict],
) -> str:
    """组装关系判定 prompt（evidence 为 {position, count} 共现摘要）。"""
    evidence_text = "、".join(
        f"{e.get('position')}({e.get('count')})" for e in evidence[:12]
    ) or "（无共现证据）"
    return _TASK_TEMPLATE.format(
        source=(source or "").strip(),
        target=(target or "").strip(),
        evidence=evidence_text,
    )


def skill_relation_gate(
    decision: SkillRelationDecision,
    source: str,
    target: str,
    known_skills: set[str],
) -> tuple[bool, str]:
    """关系硬门：防自指/虚构目标/方向与类型不匹配。返回 (gate_ok, reason)。"""
    if decision.relation == REL_NONE:
        return True, ""
    target = (target or "").strip()
    if not target:
        return False, "目标技能为空"
    if (source or "").strip() == target:
        return False, "自指关系（源=目标）"
    if target not in known_skills:
        return False, "目标技能不在已知技能集合内（虚构节点）"
    if decision.relation in (REL_PREREQUISITE, REL_BELONGS) and decision.direction != "a_to_b":
        return False, "先修/父子关系仅允许 a_to_b"
    return True, ""


def prerequisite_cycle_would_create(
    prerequisite_map: dict[str, set[str]],
    source: str,
    target: str,
) -> bool:
    """先修环判定（纯函数）：新增 (source→target) 后，target 若可沿既有先修
    入边达到 source，即成环（source 的父集含 target，新边又让 target 依赖
    source）。执行写入前运行，返回 True 表示会成环。
    """
    if (source or "").strip() == (target or "").strip():
        return True
    visited: set[str] = set()
    stack = [source]

    def parents_of(name: str) -> set[str]:
        return prerequisite_map.get(name, set())

    while stack:
        node = stack.pop()
        if node in visited:
            continue
        visited.add(node)
        for parent in parents_of(node):
            if parent == target:
                return True
            stack.append(parent)
    return False


def decide_skill_relation(
    source: str,
    target: str,
    evidence: list[dict],
    llm,
    *,
    entity_ref: str = "",
    timeout: int = DECIDE_TIMEOUT_SECONDS,
) -> Optional[SkillRelationDecision]:
    """单对关系决策；LLM 未配置/失败返回 None（proposal 通道跳过不阻塞）。"""
    if llm is None or not (source or "").strip() or not (target or "").strip():
        return None
    prompt = build_skill_relation_prompt(source, target, evidence)
    try:
        with invocation_scope(
            "skill_relation", entity_ref=entity_ref or f"skill:{source[:20]}->{target[:20]}",
        ):
            return llm.extract_structured(
                prompt,
                SkillRelationDecision,
                system_prompt=SYSTEM_PROMPT,
                timeout=timeout,
            )
    except LLMExtractionError:
        return None


def tier_for_relation_decision(
    decision: SkillRelationDecision,
    gate_ok: bool,
) -> tuple[str, str]:
    """技能关系决策风险档位：关系写入属高风险 → 一律 R2（人工审核）。"""
    return risk_tier_for(
        DOMAIN_SKILL_RELATION,
        "add_prerequisite" if decision.relation == REL_PREREQUISITE else "add_belongs_to",
        gate_ok=gate_ok,
        confidence=decision.confidence,
    )