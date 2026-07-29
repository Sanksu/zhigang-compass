"""匹配权重加载（设计文档 9.3 节）。

权重从 `configs/match_weights.json` 加载，不存在时使用默认值 (0.6, 0.2, 0.2)。
Optuna 搜索结果可覆盖默认值。
"""

import json
from pathlib import Path

# 默认权重：必备技能 0.6 / 加分技能 0.2 / 经验 0.2
DEFAULT_WEIGHTS = (0.6, 0.2, 0.2)

# 配置文件路径（相对 backend 根目录）
_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "match_weights.json"


def load_weights() -> tuple[float, float, float]:
    """加载运行时权重 (w_must, w_nice, w_exp)。

    文件不存在或字段缺失时返回默认值，不抛异常——权重缺失不应阻断匹配流程。
    """
    if not _CONFIG_PATH.exists():
        return DEFAULT_WEIGHTS
    try:
        data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        w_must = float(data.get("w_must", DEFAULT_WEIGHTS[0]))
        w_nice = float(data.get("w_nice", DEFAULT_WEIGHTS[1]))
        w_exp = float(data.get("w_exp", DEFAULT_WEIGHTS[2]))
    except (json.JSONDecodeError, TypeError, ValueError):
        return DEFAULT_WEIGHTS
    return (w_must, w_nice, w_exp)
