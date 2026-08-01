"""置信度计算（设计文档 7.2.4 节）。

三维加权基础置信度 + 学术/社区加分 + Wilson score 冷启动兜底。
"""

import json
from math import sqrt
from pathlib import Path

from app.services.discovery.schemas import ConfidenceScore

# 默认权重（可由 configs/discovery_weights.json 覆盖）
DEFAULT_W_COUNT = 0.4
DEFAULT_W_SOURCE = 0.3
DEFAULT_W_GROWTH = 0.3

# 归一化饱和点
NORM_JD_COUNT_MAX = 10     # 10 条 JD 即满分
NORM_GROWTH_RATE_MAX = 0.5  # 50% 增长率即满分
NORM_SOURCE_MAX = 4         # 4 个独立源即满分

# 冷启动判定阈值
WILSON_COLD_START_THRESHOLD = 0.3

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


def _load_weights() -> tuple[float, float, float]:
    """加载置信度权重 (w_count, w_source, w_growth)。"""
    if not _CONFIG_PATH.exists():
        return (DEFAULT_W_COUNT, DEFAULT_W_SOURCE, DEFAULT_W_GROWTH)
    try:
        data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        return (
            float(data.get("w_count", DEFAULT_W_COUNT)),
            float(data.get("w_source", DEFAULT_W_SOURCE)),
            float(data.get("w_growth", DEFAULT_W_GROWTH)),
        )
    except (json.JSONDecodeError, TypeError, ValueError):
        return (DEFAULT_W_COUNT, DEFAULT_W_SOURCE, DEFAULT_W_GROWTH)


def _norm(value: float, max_value: float) -> float:
    """归一化至 [0, 1]（负增长等负值钳制为 0，避免置信度为负）。"""
    if max_value <= 0:
        return 0.0
    return min(max(value, 0.0) / max_value, 1.0)


def compute_confidence(
    jd_count: int,
    source_count: int,
    growth_rate: float,
    arxiv_anomaly: bool = False,
    github_anomaly: bool = False,
) -> ConfidenceScore:
    """计算综合置信度（设计文档 7.2.4 节公式）。

    confidence = w_count × norm(jd_count) + w_source × source_diversity + w_growth × norm(growth_rate)
    学术/社区加分：单异常 +0.10，双异常 +0.15，封顶 1.0。
    """
    w_count, w_source, w_growth = _load_weights()

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

    final = min(base + bonus, 1.0)

    return ConfidenceScore(
        base_confidence=base,
        arxiv_anomaly=arxiv_anomaly,
        github_anomaly=github_anomaly,
        bonus=bonus,
        final_confidence=final,
    )
