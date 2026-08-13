"""图算法运行时配置（图算法优化方案阶段一产物）。

从 `configs/graph_algo.yaml` 加载，缺失/解析失败回退默认值（不抛异常，
与 matching/weights.py 同模式）。阶段一 Optuna 调优结果由
`scripts/graph_algo_tune.py --apply` 写回本文件，API 层默认参数随配置生效。
"""

from pathlib import Path

import yaml

# 配置文件路径（相对 backend 根目录）
_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "graph_algo.yaml"

DEFAULT_ALGORITHM = "louvain"
DEFAULT_RESOLUTION = 1.0
DEFAULT_MIN_WEIGHT = 2.0
DEFAULT_MIN_SIZE = 2

_VALID_ALGORITHMS = ("louvain", "leiden")  # leiden 为阶段二预留


def _load_config() -> dict:
    """读取配置文件，解析失败返回空 dict（缺失不阻断流程）。"""
    if not _CONFIG_PATH.exists():
        return {}
    try:
        data = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError:
        return {}


def _positive_float(value, default: float) -> float:
    """有限正数校验，非法值回退默认（防 NaN/0/负值破坏聚类行为）。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if v > 0 and v != float("inf") and v != float("nan") else default


def load_graph_algo_config() -> dict:
    """加载图算法配置（algorithm/resolution/min_weight/min_size）。

    配置缺失、解析失败或值非法时回退默认，保证 API 默认参数可运行。
    """
    data = _load_config()
    algorithm = str(data.get("algorithm", DEFAULT_ALGORITHM))
    if algorithm not in _VALID_ALGORITHMS:
        algorithm = DEFAULT_ALGORITHM
    return {
        "algorithm": algorithm,
        "resolution": _positive_float(data.get("resolution"), DEFAULT_RESOLUTION),
        "min_weight": _positive_float(data.get("min_weight"), DEFAULT_MIN_WEIGHT),
        "min_size": int(_positive_float(data.get("min_size"), DEFAULT_MIN_SIZE)),
    }
