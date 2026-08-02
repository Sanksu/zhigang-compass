"""匹配权重加载（设计文档 9.3 节）。

权重与语义阈值从 `configs/match_weights.json` 加载，不存在时使用默认值
(0.6, 0.2, 0.2) 与 sim_threshold=0.85。Optuna 搜索结果可覆盖默认值。
"""

import json
from pathlib import Path

# 默认权重：必备技能 0.6 / 加分技能 0.2 / 经验 0.2
DEFAULT_WEIGHTS = (0.6, 0.2, 0.2)
# 语义同义词匹配默认阈值（设计文档 9.3：Embedding 余弦 ≥ sim_threshold 视为匹配）
SIM_THRESHOLD_DEFAULT = 0.85

# 配置文件路径（相对 backend 根目录）
_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "match_weights.json"


def _load_config() -> dict:
    """读取权重配置文件，解析失败返回空 dict（不抛异常，缺失不阻断匹配）。"""
    if not _CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def load_weights() -> tuple[float, float, float]:
    """加载运行时权重 (w_must, w_nice, w_exp)。"""
    data = _load_config()
    return (
        float(data.get("w_must", DEFAULT_WEIGHTS[0])),
        float(data.get("w_nice", DEFAULT_WEIGHTS[1])),
        float(data.get("w_exp", DEFAULT_WEIGHTS[2])),
    )


def load_sim_threshold() -> float:
    """加载语义相似度阈值 sim_threshold。"""
    data = _load_config()
    try:
        return float(data.get("sim_threshold", SIM_THRESHOLD_DEFAULT))
    except (TypeError, ValueError):
        return SIM_THRESHOLD_DEFAULT
