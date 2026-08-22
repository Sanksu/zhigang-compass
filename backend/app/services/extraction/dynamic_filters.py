"""技能字典动态过滤层（dict-guard 机制层）。

静态字典（dictionary_data.py 停用词/别名、configs/skill_whitelist.yaml 白名单）
是 import 时加载的单一事实源；本层提供运行时可变的「动态停用词 + 动态保护」
叠加层，由 dict-guard 每日评估任务与管理端审批写入，≤30s 热生效（移植
matching/weights.py 的 TTL 缓存模式），无需重启 api/worker。

判定语义（接线于 dictionary.is_noise_skill，唯一接线点）：
1. 动态保护优先于一切停用词与启发式——守卫「解误杀」通道（静态停用词
   误伤真实技能时，审批后在此放行，如「微」误伤「微信小程序」）；
2. 动态停用词在白名单/别名标准名保护之后判定——即使写入侧硬门禁被绕过，
   白名单词也不会被动态拦截误杀（纵深防御；正常路径由 dict-guard 门禁
   保证动态停用词与白名单互斥）。

存储：backend/configs/skill_filters_dynamic.json（gitignore，与
runtime_settings.json 同模式；每次变更全量审计于 DictChangeLog 表，
稳定条目经人工确认后固化回 git 内静态字典）。文件缺失/损坏按空层处理，
不阻塞抽取链路。
"""

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_FILTERS_PATH = Path(__file__).resolve().parents[3] / "configs" / "skill_filters_dynamic.json"

# TTL 缓存（同 weights.py 口径）：is_noise_skill 位于抽取/聚合热路径，
# 30s TTL 保留热更新能力；路径变化（测试注入）立即失效
_CACHE_TTL = 30.0
_cache: dict = {}
_cache_at = 0.0
_cache_path: Path | None = None


def _empty() -> dict:
    return {"blocked": [], "protected": []}


def _load() -> dict:
    """读取动态过滤文件，缺失/损坏返回空层（不抛异常，不阻断抽取链路）。"""
    if not _FILTERS_PATH.exists():
        return _empty()
    try:
        data = json.loads(_FILTERS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError):
        logger.warning("动态过滤层配置损坏，按空层处理: %s", _FILTERS_PATH)
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    return {
        "blocked": [e for e in data.get("blocked", []) if isinstance(e, dict) and e.get("term")],
        "protected": [e for e in data.get("protected", []) if isinstance(e, dict) and e.get("term")],
    }


def _load_cached() -> dict:
    """TTL 缓存版读取（≤30s 生效；路径变化立即失效）。"""
    global _cache, _cache_at, _cache_path
    now = time.monotonic()
    if _cache_path is not _FILTERS_PATH or now - _cache_at > _CACHE_TTL:
        _cache = _load()
        _cache_at = now
        _cache_path = _FILTERS_PATH
    return _cache


def invalidate_cache() -> None:
    """强制下次访问重读文件（本进程写入后调用；其他进程 ≤30s 自动感知）。"""
    global _cache_at
    _cache_at = 0.0


def _matches(name: str, entries: list[dict]) -> bool:
    """精确匹配 + 英文大小写不敏感（中文词两路等价；英文词覆盖大小写变体）。"""
    exact = {e["term"].strip() for e in entries}
    return name in exact or name.lower() in {t.lower() for t in exact}


def is_dynamically_blocked(name: str) -> bool:
    """技能名是否被动态停用词拦截。"""
    return _matches(name, _load_cached()["blocked"])


def is_dynamically_protected(name: str) -> bool:
    """技能名是否受动态保护（优先于静态停用词与噪音启发式）。"""
    return _matches(name, _load_cached()["protected"])


def get_dynamic_terms() -> dict[str, list[dict]]:
    """当前动态层全量条目（管理端展示 / 报告用），返回副本防外部改写缓存。"""
    data = _load_cached()
    return {"blocked": list(data["blocked"]), "protected": list(data["protected"])}


def _write(data: dict) -> None:
    """直接覆写并失效本进程缓存。

    不用 tmp+os.replace 原子替换：生产以单文件 bind mount 共享宿主文件
    （docker-compose P1-1 修复），rename 覆盖挂载点会 EBUSY，与
    runtime_config.py 的 runtime_settings.json 写入同口径。写入中途被读的
    窗口由 _load 的损坏兜底承接（按空层处理，≤30s TTL 自愈）。
    """
    _FILTERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    data["version"] = int(data.get("version", 0)) + 1
    _FILTERS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    invalidate_cache()


def add_entry(kind: str, term: str, *, reason: str, source: str, operator: str = "") -> None:
    """新增动态条目（kind: "blocked" | "protected"）；已存在则更新元数据。

    调用方（dict-guard 分级生效 / 管理端审批）负责硬门禁与 DictChangeLog
    审计，本函数只做存储层变更。
    """
    if kind not in ("blocked", "protected"):
        raise ValueError(f"kind 必须为 blocked/protected，收到: {kind}")
    term = term.strip()
    if not term:
        raise ValueError("term 不能为空")
    data = _load()
    entries = [e for e in data[kind] if e["term"].lower() != term.lower()]
    entries.append({
        "term": term,
        "reason": reason,
        "source": source,
        "operator": operator,
        "added_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    data[kind] = entries
    _write(data)


def remove_entry(kind: str, term: str) -> bool:
    """移除动态条目（回滚通道）；目标不存在返回 False。"""
    if kind not in ("blocked", "protected"):
        raise ValueError(f"kind 必须为 blocked/protected，收到: {kind}")
    data = _load()
    kept = [e for e in data[kind] if e["term"].lower() != term.strip().lower()]
    if len(kept) == len(data[kind]):
        return False
    data[kind] = kept
    _write(data)
    return True
