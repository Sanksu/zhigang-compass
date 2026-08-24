"""岗位名 LLM 第二轮审查（幻觉防控第四道防线，《岗位名LLM审查设计方案》M2）。

定位：规则层（normalize_position_name/停用词/白名单/技能路由）始终先执行，
本模块只审查「规则原样放行的未知低频岗位名」，覆盖长尾碎片/泛词：

- 触发：归一化非空 且 归一化结果不在规则白名单（规则未分类）且 历史频次 < 5
- 单条调用，15s 超时；LLM 不可用/失败静默降级（保留原名，与 RAG 接地同语义）
- 决策表（§4.4）：invalid → 置空不入图；修正名必须过 normalize 校验，
  不一致只标记不采用；审查结果一律落 snapshot["position_review"] 供审计
- 审查结果不写规则库（雪球效应防护），规则变更仍走人工确认
- 默认关闭（runtime_config.position_review_enabled），先实验后灰度

红线（AGENTS.md §4.1）：prompt 与门控属算法核心，变更须算法岗张恺天 review。
"""

from datetime import datetime, timezone, timedelta
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.services.extraction.dictionary import (
    _POSITION_WHITELIST,
    normalize_position_name,
)
from app.services.extraction.llm_invocation import invocation_scope
from app.services.extraction.llm_provider import LLMExtractionError

# 低频门槛：历史 JD 频次达到该值视为市场已验证，不审（§4.1）
REVIEW_FREQ_MAX = 5
# 单条审查超时（s，§4.1）
REVIEW_TIMEOUT_SECONDS = 15

_CST = timezone(timedelta(hours=8))

SYSTEM_PROMPT = """你是岗位名质量审查助手。判断给定的岗位名是否为有效的标准岗位名，
并给出修正建议。只依据通用招聘市场常识判定，不臆造。不确定时 valid=true 保守保留
（宁可不审，不可误杀）。"""

_TASK_TEMPLATE = """任务：判断岗位名 "{name}"（该岗位 JD 包含技能：{skills}）

输出 JSON：
{{
  "valid": bool,
  "category": "standard|generic|abbreviation|company|gibberish|other",
  "standard_name": str | null,
  "reason": str
}}

字段说明：
- valid：是否为有效岗位名
- category：standard=有效标准名；generic=泛词（如"AI应用""IT支持"）；
  abbreviation=缩写/产品名当岗位（如 GTM、Salesforce）；company=公司名；
  gibberish=荒谬组合/拼凑；other=其他无效
- standard_name：valid=true 且可修正时给出标准岗位名（如技能含"计算机视觉"的
  "AI应用" → "机器视觉算法工程师"）；无需修正给 null

要求：
1. 只识别"岗位"名——工具/平台/缩写/公司名不是岗位名
2. 泛词若技能可明确方向可给 standard_name
3. 不确定时 valid=true 保守保留
"""


class PositionReviewResult(BaseModel):
    """LLM 审查输出（Pydantic 强校验，幻觉防控第一道防线）。"""

    valid: bool = Field(description="是否为有效岗位名")
    category: Literal[
        "standard", "generic", "abbreviation", "company", "gibberish", "other"
    ] = Field(description="无效类型归类")
    standard_name: Optional[str] = Field(default=None, description="可修正时的标准岗位名")
    reason: str = Field(default="", description="判断依据（一句话）")


def should_review(normalized: str, frequency: int) -> bool:
    """是否需要 LLM 审查：规则放行的非白名单名且低频。

    normalized 为空说明已被规则拦截（不入图），无需审查；
    normalized 命中 _POSITION_WHITELIST 说明规则已分类为标准岗位族，不审。
    """
    if not normalized or normalized in _POSITION_WHITELIST:
        return False
    return frequency < REVIEW_FREQ_MAX


def review_position_name(
    name: str,
    skills: list[str],
    llm,
    timeout: int = REVIEW_TIMEOUT_SECONDS,
) -> PositionReviewResult | None:
    """单条岗位名审查；LLM 未配置/调用失败返回 None（降级保留原名）。"""
    if llm is None or not name.strip():
        return None
    skill_text = "、".join(skills[:10]) if skills else "无"
    prompt = _TASK_TEMPLATE.format(name=name.strip(), skills=skill_text)
    try:
        with invocation_scope("position_review"):
            return llm.extract_structured(
                prompt,
                PositionReviewResult,
                system_prompt=SYSTEM_PROMPT,
                timeout=timeout,
            )
    except LLMExtractionError:
        # 超时/熔断/校验失败：静默降级保留原名（§4.4），不阻塞抽取管线
        return None


def apply_decision(
    result: PositionReviewResult | None,
    raw_name: str,
    skills: list[str],
) -> tuple[str, dict | None]:
    """决策表（§4.4）：返回 (最终岗位名, position_review 落库记录 or None)。

    - result None（降级）：保留原名，不落记录
    - valid=false：置空不入图（import_jd 对空岗位名天然跳过）
    - standard_name 必须通过 normalize_position_name 校验才采用，
      未通过只标记（standard_rejected）不采用——不一致以规则为准
    """
    reviewed_at = datetime.now(_CST).isoformat(timespec="seconds")
    if result is None:
        return raw_name, None

    base = {
        "original": raw_name,
        "category": result.category,
        "reason": result.reason,
        "reviewed_at": reviewed_at,
    }
    if not result.valid:
        return "", {**base, "valid": False, "standard_name": None}

    standard = (result.standard_name or "").strip()
    if standard:
        norm = normalize_position_name(standard, skills=skills)
        if norm:
            return norm, {**base, "valid": True, "standard_name": norm}
        return raw_name, {
            **base, "valid": True, "standard_name": None, "standard_rejected": standard,
        }
    return raw_name, {**base, "valid": True, "standard_name": None}


def extraction_skills(skills: list, requirements: list) -> list[str]:
    """抽取结果的技能名列表（去重保序），供审查 prompt 与修正名校验使用。"""
    out: list[str] = []
    for s in skills or []:
        name = getattr(s, "name", "") or ""
        if name and name not in out:
            out.append(name)
    for r in requirements or []:
        name = getattr(r, "skill_name", "") or ""
        if name and name not in out:
            out.append(name)
    return out


def select_experiment_candidates(
    rows: list[dict], limit: int = 50
) -> list[str]:
    """M1 实验抽样（§6 阶段一）：图谱低频非标准岗位名 → 待人工核对清单。

    rows 来自 Neo4j（name/req_count，已按引用升序）——与线上 should_review
    触发门同口径：归一化非空、不在规则白名单、引用数 < REVIEW_FREQ_MAX。
    """
    candidates: list[str] = []
    seen: set[str] = set()
    for r in rows:
        name = (r.get("name") or "").strip()
        if len(name) < 2 or name in seen:
            continue
        norm = normalize_position_name(name)
        if not should_review(norm, frequency=int(r.get("req_count") or 0)):
            continue
        seen.add(name)
        candidates.append(name)
        if len(candidates) >= limit:
            break
    return candidates
