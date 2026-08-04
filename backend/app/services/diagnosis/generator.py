"""诊断报告生成器（设计文档 §9.5 节）。

LLM 将匹配结果 + 差距 + 学习路径作为 context 生成结构化诊断报告。
实时路径（GET /match/result/{id}/diagnosis）使用 call_sync（单 provider 10s 上限）；
LLM 不可用/超时抛 LLMConfigurationError / LLMTimeoutError，由 API 层转 503
（诊断是增强功能，不阻断匹配主流程）。
"""

from typing import Optional

from app.services.diagnosis.prompts import (
    DIAGNOSIS_SYSTEM_PROMPT,
    DIAGNOSIS_TASK_TEMPLATE,
)
from app.services.diagnosis.schemas import DiagnosisReport
from app.services.extraction.llm_provider import LLMProviderChain

# 报告上下文裁剪上限：控制 prompt 长度，避免超出上下文窗口
_TOP_GAPS = 5
_TOP_PATH = 5
_TOP_EVIDENCE = 10


def _render_gaps(gaps: list[dict]) -> str:
    """差距 → 单行列表（技能 + 类型 + 优先级）。"""
    lines = []
    for g in gaps[:_TOP_GAPS]:
        lines.append(
            f"- {g.get('skill', '')}（{g.get('gap_type', '')}，"
            f"优先级 {g.get('priority', '')}）"
        )
    return "\n".join(lines) or "无"


def _render_path(items: list[dict]) -> str:
    """学习路径 → 单行列表（技能 + 学时 + 推荐课程）。"""
    lines = []
    for it in items[:_TOP_PATH]:
        courses = "、".join(
            c.get("title", "") for c in (it.get("courses") or [])
        ) or "无推荐课程"
        lines.append(
            f"- {it.get('skill', '')}：约 {it.get('estimated_hours', 0)} 学时，"
            f"课程：{courses}"
        )
    return "\n".join(lines) or "无"


def _render_evidence(evidence: list[dict]) -> str:
    """证据引用 → 单行列表（技能 → 来源）。"""
    lines = []
    for e in evidence[:_TOP_EVIDENCE]:
        src = e.get("url") or e.get("source") or ""
        lines.append(f"- {e.get('skill', '')}：{src}")
    return "\n".join(lines) or "无"


def generate_diagnosis(
    data: dict, llm: Optional[LLMProviderChain] = None
) -> DiagnosisReport:
    """基于匹配结果快照生成诊断报告。

    Args:
        data: compare 结果快照（match/result/{id} 的 data：分数 + gaps +
            learning_path + evidence_refs）
        llm: LLMProviderChain（测试可注入桩）；缺省实时链

    Raises:
        LLMConfigurationError / LLMTimeoutError：LLM 不可用或超时
    """
    chain = llm or LLMProviderChain()
    prompt = DIAGNOSIS_TASK_TEMPLATE.format(
        position_name=data.get("position_name", ""),
        total_score=float(data.get("total_score", 0)),
        must_score=float(data.get("must_score", 0)),
        nice_score=float(data.get("nice_score", 0)),
        exp_score=float(data.get("exp_score", 0)),
        matched="、".join(data.get("matched_must") or []) or "无",
        missing="、".join(data.get("missing_must") or []) or "无",
        gaps=_render_gaps(data.get("gaps") or []),
        path=_render_path(data.get("learning_path") or []),
        evidence=_render_evidence(data.get("evidence_refs") or []),
    )
    return chain.call_sync(
        prompt, DiagnosisReport, system_prompt=DIAGNOSIS_SYSTEM_PROMPT
    )
