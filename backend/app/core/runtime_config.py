"""运行时配置（08-16：管理后台可编辑、持久化、重启生效）。

存储：backend/configs/runtime_settings.json（gitignore，.example 入库）。
生效方式：各消费点在启动/import 时经 get() 读取——api/worker 容器重启后生效。

安全边界：仅暴露非敏感运行参数（任务并发/超时、告警 webhook、演化缓存 TTL、
采集上限、爬虫限频）；密钥/连接串/认证类配置不入此文件（保持 env 唯一事实源）。
"""

import json
import threading
from pathlib import Path

_RUNTIME_CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs" / "runtime_settings.json"

_lock = threading.Lock()
_cache: dict | None = None

# 默认值（与代码内常量一致；文件缺失/损坏时回退）
DEFAULTS: dict = {
    "arq_concurrency": 10,          # ARQ 任务并发数（tasks.WorkerSettings）
    "arq_job_timeout": 1800,        # ARQ 任务超时秒（全局，per-function 可放宽）
    "alert_webhook_url": "",        # 爬虫失败/数据过期告警 webhook
    "evolution_cache_ttl": 60,      # 演化列表缓存 TTL 秒
    "crawl_items_cap": 100,         # 爬虫单次采集条数上限（可超量源）
    "rate_limit": {},               # 爬虫限频覆盖：source -> {req_per_min, delay_range:[min,max]}
}

_VALIDATORS = {
    "arq_concurrency": lambda v: isinstance(v, int) and 1 <= v <= 100,
    "arq_job_timeout": lambda v: isinstance(v, int) and 60 <= v <= 86400,
    "alert_webhook_url": lambda v: isinstance(v, str) and (not v or v.startswith(("http://", "https://"))),
    "evolution_cache_ttl": lambda v: isinstance(v, int) and 5 <= v <= 3600,
    "crawl_items_cap": lambda v: isinstance(v, int) and 10 <= v <= 1000,
}


def _read_file() -> dict:
    """读取配置文件（缺失/损坏回退默认）。"""
    try:
        data = json.loads(_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return dict(DEFAULTS)
        return data
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULTS)


def _load() -> dict:
    global _cache
    if _cache is None:
        with _lock:
            if _cache is None:
                data = _read_file()
                _cache = {k: data.get(k, v) for k, v in DEFAULTS.items()}
    return _cache


def get(key: str, default=None):
    """读取生效配置（文件缺失时返回默认）。"""
    return _load().get(key, default)


def load_all() -> dict:
    """完整配置（供 GET 与管理后台展示）。"""
    return dict(_load())


def _validate_rate_limit(value) -> dict:
    if not isinstance(value, dict):
        raise ValueError("rate_limit 必须是对象")
    cleaned = {}
    for source, cfg in value.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"rate_limit.{source} 必须是对象")
        if "req_per_min" in cfg:
            rpm = cfg["req_per_min"]
            if not isinstance(rpm, int) or not 1 <= rpm <= 600:
                raise ValueError(f"rate_limit.{source}.req_per_min 须为 1-600 的整数")
        dr = cfg.get("delay_range")
        if dr is not None:
            if not (isinstance(dr, (list, tuple)) and len(dr) == 2
                    and all(isinstance(x, int) and 1 <= x <= 300 for x in dr)):
                raise ValueError(f"rate_limit.{source}.delay_range 须为 [min,max] 秒（1-300）")
            if dr[0] > dr[1]:
                raise ValueError(f"rate_limit.{source}.delay_range min 不能大于 max")
            dr = [int(dr[0]), int(dr[1])]
        entry = {}
        if "req_per_min" in cfg:
            entry["req_per_min"] = int(cfg["req_per_min"])
        if dr is not None:
            entry["delay_range"] = dr
        if entry:
            cleaned[source] = entry
    return cleaned


def save(values: dict) -> dict:
    """校验并持久化；返回规范化后的完整配置（校验失败抛 ValueError）。

    增量合并语义（08-16 拆页后各页只提交自己的字段）：未提供的键保留
    文件现有值，不重置为默认——避免任务页保存覆盖采集页配置。
    """
    with _lock:
        data = _read_file()
        for key, default in DEFAULTS.items():
            if key not in values:
                continue
            v = values[key]
            if key == "rate_limit":
                data[key] = _validate_rate_limit(v)
            else:
                validator = _VALIDATORS[key]
                if not validator(v):
                    raise ValueError(f"{key} 取值不合法")
                data[key] = v
        _RUNTIME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _RUNTIME_CONFIG_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        global _cache
        _cache = data
        return dict(data)
