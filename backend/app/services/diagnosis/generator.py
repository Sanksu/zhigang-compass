"""诊断报告生成器（设计文档 §9.5 / §6.4 节）。

LLM 将匹配结果 + 差距 + 学习路径 + 图谱参考上下文作为 context 生成结构化诊断报告。
实时路径（GET /match/result/{id}/diagnosis）使用 call_sync（单 provider 10s 上限）；
LLM 不可用/超时抛 LLMConfigurationError / LLMTimeoutError，由 API 层转 503
（诊断是增强功能，不阻断匹配主流程）。

图谱参考上下文由通用 RAG 检索模块（services/rag/retrieval.py）在 API 层动态检索后
注入（§6.4：岗位定义 + 技能描述 + 历史诊断报告，3000 token 截断，evidence_id 追溯）。
"""

from typing import Optional

from app.services.diagnosis.prompts import (
    DIAGNOSIS_SYSTEM_PROMPT,
    DIAGNOSIS_TASK_TEMPLATE,
)
from app.services.diagnosis.schemas import DiagnosisReport
from app.services.extraction.llm_provider import LLMProviderChain
from app.services.rag.retrieval import allowed_evidence_ids

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


def _render_rag_context(chunks: list[dict]) -> str:
    """图谱参考上下文（RAG 检索命中）→ 单行列表（内容 + evidence_id）。"""
    lines = []
    for c in chunks:
        lines.append(f"- {c.get('content', '')}（evidence_id: {c.get('evidence_id', '')}）")
    return "\n".join(lines) or "无"


def generate_diagnosis(
    data: dict,
    llm: Optional[LLMProviderChain] = None,
    rag_chunks: Optional[list[dict]] = None,
) -> DiagnosisReport:
    """基于匹配结果快照生成诊断报告。

    Args:
        data: compare 结果快照（match/result/{id} 的 data：分数 + gaps +
            learning_path + evidence_refs）
        llm: LLMProviderChain（测试可注入桩）；缺省实时链
        rag_chunks: 通用 RAG 检索模块返回的图谱上下文命中（RetrievedChunk 的
            dict 形态：content + evidence_id），缺省空列表（上下文渲染为"无"）

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
        rag_context=_render_rag_context(rag_chunks or []),
    )
    report = chain.call_sync(
        prompt, DiagnosisReport, system_prompt=DIAGNOSIS_SYSTEM_PROMPT
    )
    # 虚构引用后置拦截（§6.4 生成约束）：断言引用的 evidence_id 必须能追溯
    # 到 RAG 上下文或匹配快照证据，否则视为 LLM 编造，置空避免前端点击死链
    allowed = allowed_evidence_ids(rag_chunks or [], data.get("evidence_refs") or [])
    for gap in report.top_gaps:
        if gap.evidence_id and gap.evidence_id not in allowed:
            gap.evidence_id = ""
    return report
