"""数据质量过滤阈值加载（设计文档 §4.2/§4.7）。

SimHash 汉明阈值、Embedding 语义去重阈值、时滞检测（SAI/僵尸/抄袭）阈值
从 `configs/data_quality_thresholds.json` 加载，不存在时使用模块默认值。
TTL 缓存（30s，与 matching/weights.py 同模式）：采集/聚合热循环内避免
每次调用读文件；修改配置 ≤30s 生效，路径变化（测试注入/配置切换）立即失效。
"""

import json
import time
from pathlib import Path

# ── 默认值（设计文档 §4.2/§4.7）──
DEFAULT_HAMMING_THRESHOLD = 3
DEFAULT_EMBED_DEDUP_THRESHOLD = 0.9
DEFAULT_SAI_STALE = 1.5
DEFAULT_SAI_OBSOLETE = 2.0
DEFAULT_RECENT_WINDOW_DAYS = 90
DEFAULT_ZOMBIE_JACCARD = 0.95
DEFAULT_ZOMBIE_SAI = 1.5
DEFAULT_ZOMBIE_PERIODS = 4
DEFAULT_PLAGIARISM_DAYS = 90

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "data_quality_thresholds.json"

_CONFIG_CACHE_TTL = 30.0
_config_cache: dict = {}
_config_cache_at = 0.0
_config_cache_path: Path | None = None


def _load_config() -> dict:
    """读取阈值配置文件，解析失败返回空 dict（缺失不阻断检测）。"""
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


def _get(key: str, default: float | int) -> float | int:
    data = _load_config_cached()
    try:
        return data.get(key, default)
    except (TypeError, ValueError):
        return default


def load_hamming_threshold() -> int:
    """SimHash 汉明距离阈值（≤ threshold 判定近似重复）。"""
    return int(_get("hamming_threshold", DEFAULT_HAMMING_THRESHOLD))


def load_embed_dedup_threshold() -> float:
    """jd_embeddings 语义去重辅助阈值（Cosine < 阈值视为语义不相似不标记）。"""
    return float(_get("embed_dedup_threshold", DEFAULT_EMBED_DEDUP_THRESHOLD))


def load_sai_stale_threshold() -> float:
    return float(_get("sai_stale_threshold", DEFAULT_SAI_STALE))


def load_sai_obsolete_threshold() -> float:
    return float(_get("sai_obsolete_threshold", DEFAULT_SAI_OBSOLETE))


def load_recent_window_days() -> int:
    return int(_get("recent_window_days", DEFAULT_RECENT_WINDOW_DAYS))


def load_zombie_jaccard_threshold() -> float:
    return float(_get("zombie_jaccard_threshold", DEFAULT_ZOMBIE_JACCARD))


def load_zombie_sai_threshold() -> float:
    return float(_get("zombie_sai_threshold", DEFAULT_ZOMBIE_SAI))


def load_zombie_consecutive_periods() -> int:
    return int(_get("zombie_consecutive_periods", DEFAULT_ZOMBIE_PERIODS))


def load_plagiarism_days() -> int:
    return int(_get("plagiarism_days", DEFAULT_PLAGIARISM_DAYS))
