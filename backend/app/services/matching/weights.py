"""匹配权重加载（设计文档 9.3 节）。

权重与语义阈值从 `configs/match_weights.json` 加载，不存在时使用默认值
(0.6, 0.2, 0.2) 与 sim_threshold=0.85。Optuna 搜索结果可覆盖默认值。
"""

import json
import time
from pathlib import Path

# 默认权重：必备技能 0.6 / 加分技能 0.2 / 经验 0.2
DEFAULT_WEIGHTS = (0.6, 0.2, 0.2)
# 语义同义词匹配默认阈值（设计文档 9.3：Embedding 余弦 ≥ sim_threshold 视为匹配）
SIM_THRESHOLD_DEFAULT = 0.85

# 配置文件路径（相对 backend 根目录）
_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "match_weights.json"
# 领域跨簇语义黑名单配置（独立文件便于动态调整，修改即时生效）
_BLOCKLIST_PATH = Path(__file__).resolve().parents[3] / "configs" / "domain_sem_blocklist.json"

# 配置 TTL 缓存（08-14 审查：评分热循环内每技能×岗位读文件 → 单次匹配数千次 IO；
# 30s TTL 保留热更新能力，新进程/评测冷启动必然读到最新配置；路径变化（测试
# 注入/配置切换）立即失效）
_CONFIG_CACHE_TTL = 30.0
_config_cache: dict = {}
_config_cache_at = 0.0
_config_cache_path: Path | None = None
_blocklist_cache: dict = {}
_blocklist_cache_at = 0.0
_blocklist_cache_path: Path | None = None


def _load_config() -> dict:
    """读取权重配置文件，解析失败返回空 dict（不抛异常，缺失不阻断匹配）。"""
    if not _CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def _load_config_cached() -> dict:
    """TTL 缓存版配置读取（≤30s 生效；配置路径变化立即失效）。"""
    global _config_cache, _config_cache_at, _config_cache_path
    now = time.monotonic()
    if _config_cache_path is not _CONFIG_PATH or now - _config_cache_at > _CONFIG_CACHE_TTL:
        _config_cache = _load_config()
        _config_cache_at = now
        _config_cache_path = _CONFIG_PATH
    return _config_cache


def _valid_weights(weights: tuple[float, float, float]) -> bool:
    """权重合法要求：三个值均为有限非负数，且和 > 0（全 0 会导致匹配总分恒为 0）。"""
    return all(
        isinstance(w, float) and w >= 0.0 and w != float("inf") and w != float("nan")
        for w in weights
    ) and sum(weights) > 0.0


def load_weights() -> tuple[float, float, float]:
    """加载运行时权重 (w_must, w_nice, w_exp)。

    配置缺失、解析失败或权重全 0 时回退默认权重，防止匹配总分恒为 0。
    """
    data = _load_config_cached()
    try:
        weights = (
            float(data.get("w_must", DEFAULT_WEIGHTS[0])),
            float(data.get("w_nice", DEFAULT_WEIGHTS[1])),
            float(data.get("w_exp", DEFAULT_WEIGHTS[2])),
        )
    except (TypeError, ValueError):
        return DEFAULT_WEIGHTS
    return weights if _valid_weights(weights) else DEFAULT_WEIGHTS


def load_sim_threshold() -> float:
    """加载语义相似度阈值 sim_threshold。"""
    data = _load_config_cached()
    try:
        return float(data.get("sim_threshold", SIM_THRESHOLD_DEFAULT))
    except (TypeError, ValueError):
        return SIM_THRESHOLD_DEFAULT


# 领域跨簇语义黑名单默认（与 configs/domain_sem_blocklist.json 缺失时保持一致）
DOMAIN_BLOCKLIST_DEFAULT = (("制造业", "电商"),)


def _load_blocklist_config() -> dict:
    """读取领域黑名单配置文件，解析失败返回空 dict（缺失不阻断匹配）。"""
    if not _BLOCKLIST_PATH.exists():
        return {}
    try:
        data = json.loads(_BLOCKLIST_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def _load_blocklist_cached() -> dict:
    """TTL 缓存版黑名单读取（≤30s 生效；路径变化立即失效）。"""
    global _blocklist_cache, _blocklist_cache_at, _blocklist_cache_path
    now = time.monotonic()
    if _blocklist_cache_path is not _BLOCKLIST_PATH or now - _blocklist_cache_at > _CONFIG_CACHE_TTL:
        _blocklist_cache = _load_blocklist_config()
        _blocklist_cache_at = now
        _blocklist_cache_path = _BLOCKLIST_PATH
    return _blocklist_cache


def load_domain_sem_blocklist() -> frozenset:
    """加载领域跨簇语义黑名单为无序对集合（frozenset 成员双向等价）。

    配置格式：{"pairs": [["制造业", "电商"], ...]}。缺失/损坏/空回退默认
    （制造业×电商）。TTL 缓存（30s）：引擎逐岗位调用，避免每次匹配
    数千次文件 IO；修改配置 ≤30s 生效。
    """
    data = _load_blocklist_cached()
    try:
        pairs = data.get("pairs")
        if not isinstance(pairs, list) or not pairs:
            return frozenset(frozenset(p) for p in DOMAIN_BLOCKLIST_DEFAULT)
        pairs_set = set()
        for pair in pairs:
            if isinstance(pair, (list, tuple)) and len(pair) == 2 and all(isinstance(x, str) and x for x in pair):
                pairs_set.add(frozenset(x.lower() for x in pair))
        return frozenset(pairs_set) if pairs_set else frozenset(frozenset(p) for p in DOMAIN_BLOCKLIST_DEFAULT)
    except (TypeError, ValueError):
        return frozenset(frozenset(p) for p in DOMAIN_BLOCKLIST_DEFAULT)
