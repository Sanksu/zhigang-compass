"""运行时配置（08-16：管理后台可编辑、持久化、重启生效）。

存储：backend/configs/runtime_settings.json（gitignore，.example 入库）。
生效方式：各消费点在启动/import 时经 get() 读取——api/worker 容器重启后生效。

安全边界：仅暴露非敏感运行参数（任务并发/超时、告警 webhook、演化缓存 TTL、
采集上限、爬虫限频、ETL 批次与调度时间）；密钥/连接串/认证类配置不入此文件
（保持 env 唯一事实源）。
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
    # ETL 队列（08-21 配置中心新增：批次上限/默认批次 + 容器内 ARQ cron 调度时间）
    "etl_batch_cap": 2000,              # ETL 批次上限（积压缩放封顶，etl.py _etl_limit）
    "etl_structure_load_default": 500,  # 结构化加载默认批次（batch_extract）
    "etl_validate_temporal_default": 200,  # 时滞/通胀检测默认批次
    "etl_run_hour": 5,                  # ETL 调度小时（0-23，容器内 ARQ cron）
    "etl_run_minute": 0,                # ETL 调度分钟（0-59）
    # 每爬虫采集配置（08-21）：spider -> {enabled, max_results, max_empty_retries}；
    # 缺省启用、按源默认数量；max_empty_retries=0 关闭页面级空列表退避重试（默认 3）
    "crawlers": {},
    # dict-guard 字典守卫（技能字典自治守卫方案）：每日评估图谱数据分级调整字典过滤
    "dict_guard_enabled": True,             # 总开关（false 时 ETL 阶段直接跳过）
    "dict_guard_auto_impact_threshold": 50,  # 自动生效影响面上限（图谱节点数，超过转人工）
    "dict_guard_min_confidence": 0.8,        # 自动生效最低 LLM 置信度
    "dict_guard_max_candidates": 20,         # 每类候选上限（控制每日 LLM 成本）
    "dict_guard_reproposal_cooldown_days": 7,  # 驳回提案冷却期（天内不重提，08-24 缺口修复）
    # 岗位名 LLM 审查（幻觉防控第四道防线，方案评审稿）：默认关闭先实验后灰度
    "position_review_enabled": False,
    # 技能分类 LLM 审查（LLM 驱动化 P1）：未分类技能灰度提议，默认关闭
    "skill_category_review_enabled": False,
    "skill_category_max_candidates": 20,
}

_VALIDATORS = {
    "arq_concurrency": lambda v: isinstance(v, int) and 1 <= v <= 100,
    "arq_job_timeout": lambda v: isinstance(v, int) and 60 <= v <= 86400,
    "alert_webhook_url": lambda v: isinstance(v, str) and (not v or v.startswith(("http://", "https://"))),
    "evolution_cache_ttl": lambda v: isinstance(v, int) and 5 <= v <= 3600,
    "crawl_items_cap": lambda v: isinstance(v, int) and 10 <= v <= 1000,
    "etl_batch_cap": lambda v: isinstance(v, int) and 100 <= v <= 5000,
    "etl_structure_load_default": lambda v: isinstance(v, int) and 100 <= v <= 1000,
    "etl_validate_temporal_default": lambda v: isinstance(v, int) and 100 <= v <= 500,
    "etl_run_hour": lambda v: isinstance(v, int) and 0 <= v <= 23,
    "etl_run_minute": lambda v: isinstance(v, int) and 0 <= v <= 59,
    "dict_guard_auto_impact_threshold": lambda v: isinstance(v, int) and 1 <= v <= 1000,
    "dict_guard_min_confidence": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and 0.0 <= v <= 1.0,
    "dict_guard_max_candidates": lambda v: isinstance(v, int) and 1 <= v <= 100,
    "dict_guard_reproposal_cooldown_days": lambda v: isinstance(v, int) and 1 <= v <= 90,
    "position_review_enabled": lambda v: isinstance(v, bool),
    "skill_category_review_enabled": lambda v: isinstance(v, bool),
    "skill_category_max_candidates": lambda v: isinstance(v, int) and 1 <= v <= 100,
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


def _validate_crawlers(value) -> dict:
    """校验每爬虫采集配置：spider -> {enabled, max_results, max_empty_retries, hour, minute}。

    - value 必须是对象（spider 名 → 配置）；
    - enabled 布尔；max_results 10-1000 整数；max_empty_retries 0-10 整数（0=关闭
      页面级空列表退避重试，spider 端读取）；hour 0-23 / minute 0-59（独立触发时间，
      必须成对配置，仅配置其一视为非法）。
    返回规范化后的 dict（仅含有效字段）。
    """
    if not isinstance(value, dict):
        raise ValueError("crawlers 必须是对象")
    cleaned = {}
    for spider, cfg in value.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"crawlers.{spider} 必须是对象")
        if not isinstance(spider, str) or not spider.strip():
            raise ValueError("crawlers 键（spider 名）不能为空")
        entry = {}
        if "enabled" in cfg:
            if not isinstance(cfg["enabled"], bool):
                raise ValueError(f"crawlers.{spider}.enabled 必须是布尔值")
            entry["enabled"] = cfg["enabled"]
        if "max_results" in cfg:
            mr = cfg["max_results"]
            if not isinstance(mr, int) or not 10 <= mr <= 1000:
                raise ValueError(f"crawlers.{spider}.max_results 须为 10-1000 的整数")
            entry["max_results"] = mr
        if "max_empty_retries" in cfg:
            mer = cfg["max_empty_retries"]
            if not isinstance(mer, int) or not 0 <= mer <= 10:
                raise ValueError(f"crawlers.{spider}.max_empty_retries 须为 0-10 的整数")
            entry["max_empty_retries"] = mer
        has_hour = "hour" in cfg
        has_minute = "minute" in cfg
        if has_hour != has_minute:
            raise ValueError(f"crawlers.{spider}.hour/minute 必须成对配置")
        if has_hour:
            hour, minute = cfg["hour"], cfg["minute"]
            if not isinstance(hour, int) or not 0 <= hour <= 23:
                raise ValueError(f"crawlers.{spider}.hour 须为 0-23 的整数")
            if not isinstance(minute, int) or not 0 <= minute <= 59:
                raise ValueError(f"crawlers.{spider}.minute 须为 0-59 的整数")
            entry["hour"] = hour
            entry["minute"] = minute
        cleaned[spider.strip()] = entry
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
            elif key == "crawlers":
                data[key] = _validate_crawlers(v)
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
