"""置信度计算（设计文档 7.2.4 节）。

三维加权基础置信度 + 学术/社区加分 + 证据距离标量化（P1，08-21）。
证据距离 = 图谱证据距离（graph_grounding）× 0.5 + LLM 输出概率（llm_logprob）× 0.5：
- 图谱证据距离：候选岗位技能与图谱既有岗位 REQUIRES 共享桥的归一化程度——
  技能被越多既有岗位共用，候选越贴近图谱既有证据（证据距离越近，分数越高）；
  技能孤立/无共享桥 → 分数低（证据距离远，可能是编造/孤证）。
- LLM 输出概率：token 级 Logprob 归一化（LLM 生成定义草案的自信心）。
  当前 instructor 结构化链路未开放 Logprob 采集，缺省 None = 中性 0.5（不偏移阈值）；
  图谱距离为 P1 优先落地信号。

最终标量：final = base + bonus + w_evidence × (evidence - 0.5) × 2
证据中性（0.5）时不偏移既有 0.6/0.8 阈值（阈值校准不变）；证据 1.0 → +w_evidence，
证据 0.0 → -w_evidence（阻断低证据候选，<0.75 在前端审核队列标记需复核）。

Wilson score 冷启动兜底不变。
"""

import json
from math import sqrt
from pathlib import Path

from app.services.discovery.schemas import ConfidenceScore

# 默认权重（可由 configs/discovery_weights.json 覆盖）
DEFAULT_W_COUNT = 0.4
DEFAULT_W_SOURCE = 0.3
DEFAULT_W_GROWTH = 0.3
DEFAULT_W_EVIDENCE = 0.15

# 归一化饱和点
NORM_JD_COUNT_MAX = 10     # 10 条 JD 即满分
NORM_GROWTH_RATE_MAX = 0.5  # 50% 增长率即满分
NORM_SOURCE_MAX = 4         # 4 个独立源即满分
NORM_GRAPH_SHARED_MAX = 10  # 图谱共享技能岗位数：10 个既有岗位共用即满分（证据距离近）

# 前端阻断复核阈值（candidate-review-tab.tsx REVIEW_BLOCK_THRESHOLD 同步）：
# final_confidence < 0.75 的候选标记"需复核"，人工审核后方可晋升
REVIEW_BLOCK_THRESHOLD = 0.75

# 冷启动判定阈值（2026-08-11 调降 0.3→0.2：样本口径已改为"首现后窗口出现率"，
# 0.3 阈值下首现即出现 1 个窗口的岗位 Wilson 下界 ≈0.206 不过，低频新岗位
# 需等第 2 个窗口才被检测；0.2 允许首窗口确认后即入池）
WILSON_COLD_START_THRESHOLD = 0.2

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "discovery_weights.json"


def wilson_lower(successes: int, total: int, z: float = 1.96) -> float:
    """Wilson score 95% 置信区间下界（z=1.96 对应 95%）。

    冷启动兜底：历史窗口不足 90 天时，用 Wilson score 替代 Z-score 做保守估计。
    successes = 出现该技能的 JD 数，total = 总 JD 数。
    """
    if total == 0:
        return 0.0
    p = successes / total
    denominator = 1 + z * z / total
    center = p + z * z / (2 * total)
    spread = z * sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return max(0.0, (center - spread) / denominator)


def _load_weights() -> tuple[float, float, float, float]:
    """加载置信度权重 (w_count, w_source, w_growth, w_evidence)。"""
    if not _CONFIG_PATH.exists():
        return (DEFAULT_W_COUNT, DEFAULT_W_SOURCE, DEFAULT_W_GROWTH, DEFAULT_W_EVIDENCE)
    try:
        data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        return (
            float(data.get("w_count", DEFAULT_W_COUNT)),
            float(data.get("w_source", DEFAULT_W_SOURCE)),
            float(data.get("w_growth", DEFAULT_W_GROWTH)),
            float(data.get("w_evidence", DEFAULT_W_EVIDENCE)),
        )
    except (json.JSONDecodeError, TypeError, ValueError):
        return (DEFAULT_W_COUNT, DEFAULT_W_SOURCE, DEFAULT_W_GROWTH, DEFAULT_W_EVIDENCE)


def _norm(value: float, max_value: float) -> float:
    """归一化至 [0, 1]（负增长等负值钳制为 0，避免置信度为负）。"""
    if max_value <= 0:
        return 0.0
    return min(max(value, 0.0) / max_value, 1.0)


def graph_grounding_score(shared_position_counts: list[int]) -> float | None:
    """图谱证据距离分（0-1）：候选岗位技能与图谱既有岗位的共享程度。

    Args:
        shared_position_counts: 候选每个技能在图谱中被既有岗位 REQUIRES 共享的
            岗位数列表（worker 批量查询产出）。取技能最大值归一化——
            任一技能与大量既有岗位共用即视为证据距离近（贴近图谱既有证据）。

    Returns:
        0-1 分数；空技能/无图谱数据 → None（中性 0.5，不惩罚不奖励）。
    """
    if not shared_position_counts:
        return None
    peak = max(shared_position_counts)
    return min(max(peak / NORM_GRAPH_SHARED_MAX, 0.0), 1.0)


def evidence_score(
    graph_grounding: float | None = None,
    llm_logprob: float | None = None,
) -> float:
    """证据距离综合分（0-1）：graph_grounding 与 llm_logprob 各占 0.5。

    任一信号缺失（None）按 0.5 中性值参与——无图谱数据/未采集 Logprob 时
    不惩罚不奖励，既有 0.6/0.8 阈值不受偏移（阈值校准保持）。
    """
    g = 0.5 if graph_grounding is None else min(max(graph_grounding, 0.0), 1.0)
    l = 0.5 if llm_logprob is None else min(max(llm_logprob, 0.0), 1.0)
    return 0.5 * g + 0.5 * l


def compute_confidence(
    jd_count: int,
    source_count: int,
    growth_rate: float,
    arxiv_anomaly: bool = False,
    github_anomaly: bool = False,
    graph_grounding: float | None = None,
    llm_logprob: float | None = None,
) -> ConfidenceScore:
    """计算综合置信度（设计文档 7.2.4 节公式 + P1 证据距离标量化）。

    final = w_count×norm(jd) + w_source×source + w_growth×norm(growth)
            + 学术/社区加分 + w_evidence × (evidence - 0.5) × 2
    学术/社区加分：单异常 +0.10，双异常 +0.15。
    证据距离：evidence = 0.5×graph_grounding + 0.5×llm_logprob，中性 0.5。
    输出 0-1 标量（final_confidence），低于 REVIEW_BLOCK_THRESHOLD=0.75 的
    候选在前端候选审核队列标记"需复核"（人工审核后放行）。
    """
    w_count, w_source, w_growth, w_evidence = _load_weights()

    base = (
        w_count * _norm(jd_count, NORM_JD_COUNT_MAX)
        + w_source * _norm(source_count, NORM_SOURCE_MAX)
        + w_growth * _norm(growth_rate, NORM_GROWTH_RATE_MAX)
    )

    bonus = 0.0
    if arxiv_anomaly and github_anomaly:
        bonus = 0.15
    elif arxiv_anomaly or github_anomaly:
        bonus = 0.10

    ev = evidence_score(graph_grounding, llm_logprob)
    # 证据中性（0.5）时该项为 0，不偏移既有阈值；证据 1.0 → +w_evidence，0.0 → -w_evidence
    evidence_delta = w_evidence * (ev - 0.5) * 2

    final = min(base + bonus + evidence_delta, 1.0)

    return ConfidenceScore(
        base_confidence=base,
        arxiv_anomaly=arxiv_anomaly,
        github_anomaly=github_anomaly,
        bonus=bonus,
        graph_grounding=graph_grounding,
        llm_logprob=llm_logprob,
        evidence_score=ev,
        final_confidence=final,
    )
