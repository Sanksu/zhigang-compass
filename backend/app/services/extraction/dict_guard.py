"""技能字典自治守卫（dict-guard）评估服务：Schema / Prompt / 硬门禁 / 分级。

职责边界（对齐 cluster_llm 模式——纯逻辑在本模块，IO 编排在 workers/dict_guard.py）：
1. ``DictGuardDecision``：LLM 输出 JSON Schema（Pydantic，兼作 instructor 强校验）
2. ``build_decision_prompt``：单候选评估输入（词条 + 证据 + 动作说明）
3. ``hard_gate``：写入侧硬门禁（先于分级，一票否决）
4. ``tier_for``：分级裁决（auto / proposal / skip）

红线（AGENTS.md §4.1）：本模块的 prompt 与门禁/分级规则属算法核心，
变更须算法岗张恺天 review。设计原则对齐《岗位名LLM审查设计方案》：
LLM 增强 + 规则优先 + 门控 + 失败降级；高风险变更不自动写规则库。
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.core import runtime_config
from app.services.extraction.dictionary import (
    SKILL_STOPWORDS,
    SKILL_WHITELIST,
    SOFT_SKILL_WHITELIST,
    TOOL_ALIAS,
    _ALIAS_STANDARDS,
)
from app.services.extraction.dynamic_filters import is_dynamically_blocked


class DictGuardDecision(BaseModel):
    """LLM 对单个候选词条的裁决（instructor JSON Schema 约束）。"""

    action: Literal["add_stopword", "remove_stopword", "protect_whitelist"] = Field(
        description=(
            "add_stopword=加入停用词拦噪音；remove_stopword=从停用词移除解误杀；"
            "protect_whitelist=保留停用词但为受影响真实技能加保护"
        )
    )
    term: str = Field(description="动作作用的目标词（停用词动作填该词，保护动作填受影响的技能名）")
    reason: str = Field(default="", description="判定理由（一句话）")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="置信度 0~1")


_DECISION_PROMPT_TEMPLATE = """你是技能词典治理助手。招聘数据图谱的每日巡检产出了一个候选词条，请判断是否需要调整技能字典的过滤规则。

候选词：{term}
候选类型：{kind}
证据：
{evidence}

可选动作说明：
- add_stopword：该词不是真实技能（噪音/泛词/岗位碎片/经验短语），加入停用词拦截
- remove_stopword：该词是现行停用词但属误杀的真实技能，应移除
- protect_whitelist：该词本身该拦，但它（或包含它的真实技能名）被误伤，应为具体技能名加保护

严格按 JSON 输出，字段：action, term, reason, confidence。
要求：
1. 只依据通用技术招聘市场常识，不臆造
2. 不确定时给低 confidence（<0.8 会转人工审核，宁缺毋滥）
3. protect_whitelist 的 term 必须是具体的真实技能名，不是停用词本身
"""


def build_decision_prompt(candidate: dict) -> str:
    """构造单候选评估 prompt。candidate: {term, kind, evidence}。"""
    evidence_lines = "\n".join(f"- {k}: {v}" for k, v in (candidate.get("evidence") or {}).items())
    if not evidence_lines:
        evidence_lines = "- 无结构化证据"
    return _DECISION_PROMPT_TEMPLATE.format(
        term=candidate.get("term", ""),
        kind=candidate.get("kind", ""),
        evidence=evidence_lines,
    )


_TOOL_ALIAS_KEYS_LOWER = {k.lower() for k in TOOL_ALIAS}
_TOOL_ALIAS_VALUES = set(TOOL_ALIAS.values())


def hard_gate(action: str, term: str) -> tuple[bool, str]:
    """写入侧硬门禁（一票否决，先于分级）。

    核心不变量：动态停用词与白名单/别名标准名互斥——is_noise_skill 判定
    顺序中白名单保护在动态停用词之前（纵深防御），此处从源头拒绝互斥条目。
    """
    term = term.strip()
    if len(term) < 2:
        return False, "词条过短"
    if action == "add_stopword":
        if term in SKILL_STOPWORDS or is_dynamically_blocked(term):
            return False, "已是现行停用词"
        if term in SKILL_WHITELIST or term in _ALIAS_STANDARDS:
            return False, "命中白名单/别名标准名（停用词优先于白名单，误加即误杀）"
        if term.lower() in _TOOL_ALIAS_KEYS_LOWER or term in _TOOL_ALIAS_VALUES:
            return False, "命中工具别名表"
        if term in SOFT_SKILL_WHITELIST:
            return False, "命中软技能白名单"
        return True, ""
    if action == "remove_stopword":
        if term in SKILL_STOPWORDS or is_dynamically_blocked(term):
            return True, ""
        return False, "目标不是现行停用词（静态移除走 git 固化流程）"
    if action == "protect_whitelist":
        if term in SKILL_WHITELIST or term in _ALIAS_STANDARDS:
            return False, "已受白名单/别名标准名保护"
        if not (term in SKILL_STOPWORDS or is_dynamically_blocked(term)):
            return False, "目标未被任何停用词拦截，无需保护"
        return True, ""
    return False, f"未知 action: {action}"


def tier_for(action: str, gate_ok: bool, impact_nodes: int, confidence: float) -> str:
    """分级裁决：auto（自动生效）/ proposal（进审核池）/ skip（不处理）。

    仅 add_stopword 可自动生效（拦噪音是收紧，风险不对称地低于放行）；
    remove_stopword / protect_whitelist 一律进人工审批。
    """
    if not gate_ok:
        return "skip"
    if action != "add_stopword":
        return "proposal"
    threshold = runtime_config.get("dict_guard_auto_impact_threshold", 50)
    min_confidence = runtime_config.get("dict_guard_min_confidence", 0.8)
    if impact_nodes > threshold:
        return "proposal"
    if confidence < min_confidence:
        return "proposal"
    return "auto"


def select_suspect_skills(rows: list[dict]) -> list[dict]:
    """图谱长尾可疑技能筛选（纯函数）：低引用 + 白名单外 + 非现行停用词。

    rows 来自 Neo4j 查询（name/first_seen/category/req_count），已按引用升序。
    """
    suspects = []
    for r in rows:
        name = (r.get("name") or "").strip()
        if len(name) < 2:
            continue
        if name in SKILL_WHITELIST or name in _ALIAS_STANDARDS:
            continue
        if name in SKILL_STOPWORDS or is_dynamically_blocked(name):
            continue
        suspects.append({
            "term": name,
            "kind": "suspect_skill",
            "evidence": {
                "图谱引用数(REQUIRES)": r.get("req_count", 0),
                "首次入图": r.get("first_seen") or "未知",
                "分类": r.get("category") or "未分类",
            },
        })
    return suspects


def select_stopword_misuse(
    corpus: str, stopwords: set[str], protected_names: set[str]
) -> list[dict]:
    """停用词误杀检测（纯函数）：停用词是某受保护技能名的子串且两者都在语料出现。

    例：停用词「微」⊂「微信小程序」，语料中「微信小程序」有命中 → 「微」过宽
    误杀证据。返回候选（stopword/victim 成对），由 LLM 裁决 remove 或 protect。
    """
    misuses = []
    for victim in sorted(protected_names):
        if not victim or len(victim) < 2 or victim not in corpus:
            continue
        for sw in stopwords:
            if len(sw) < 2 or sw not in victim:
                continue
            misuses.append({
                "term": sw,
                "kind": "stopword_misuse",
                "evidence": {
                    "受影响技能": victim,
                    "该技能语料命中": corpus.count(victim),
                    "停用词语料命中": corpus.count(sw),
                },
            })
    return misuses
