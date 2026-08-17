"""管理后台路由：用户管理 / 审计日志 / 爬虫状态 / 岗位审核（RBAC admin only）。

对齐契约 /api/v1/admin/*。岗位审核（positions/pending）读取 DiscoveryCandidate 表
（默认过滤 state=candidate），review 走状态机校验 + 图谱 status 同步 + 审计日志。
"""

import asyncio
import re
from pathlib import Path

import yaml
from fastapi import APIRouter, Depends

from app.api.deps import require_permission
from app.api.v1.admin_routes import (
    accounts,
    audit,
    crawl,
    position_edit,
    position_reviews,
)
from app.core.database import redis_client
from app.core.errors import ERR_INTERNAL, ERR_NOT_FOUND, ERR_VALIDATION
from app.schemas.common import error, ok

router = APIRouter(prefix="/admin", dependencies=[Depends(require_permission("admin:*"))])
router.include_router(accounts.router)
router.include_router(audit.router)
router.include_router(crawl.router)
router.include_router(position_reviews.router)
router.include_router(position_edit.router)

# 爬虫域私有符号 re-export（tests/admin/* 直连导入）
PLATFORM_META = crawl.PLATFORM_META
_PLATFORM_TO_SPIDER = crawl._PLATFORM_TO_SPIDER
_history_row = crawl._history_row
_match_platform = crawl._match_platform
_crawl_log_events = crawl._crawl_log_events

# 岗位审核域私有符号 re-export（tests/admin/test_positions_pending、tests/matching 直连导入）
positions_pending = position_reviews.positions_pending
_persist_rejected_change = position_reviews._persist_rejected_change
_persist_position_state = position_reviews._persist_position_state

# 岗位人工编辑域私有符号 re-export（tests/admin/test_position_edit 直连导入）
validate_position_edit = position_edit.validate_position_edit
position_edit_diff = position_edit.position_edit_diff
_get_position_detail_tx = position_edit._get_position_detail_tx
_edit_position_tx = position_edit._edit_position_tx


# ============================================================
# 岗位人工编辑（设计文档 12.2：审核员直接编辑岗位定义，改动写 PositionEditLog）
# ============================================================

def _query_position_detail(position_name: str) -> dict | None:
    """岗位详情读取（Neo4j 同步驱动，线程池执行）。"""
    from app.core.database import neo4j_driver

    with neo4j_driver.session() as session:
        return session.execute_read(_get_position_detail_tx, position_name)


def _edit_position_neo4j(position_name: str, editor_id, skills, core_duties, scenarios) -> dict:
    """岗位编辑写（Neo4j 同步驱动，线程池执行）。"""
    from app.core.database import neo4j_driver

    with neo4j_driver.session() as session:
        return session.execute_write(
            _edit_position_tx, position_name, editor_id, skills, core_duties, scenarios
        )


@router.get("/positions/{position_name}")
async def get_position_detail(position_name: str):
    """岗位详情（§12.2 岗位人工编辑：编辑前查看技能/学历/证书与文本定义）。"""
    detail = await asyncio.to_thread(_query_position_detail, position_name)
    if detail is None:
        return error(ERR_NOT_FOUND, f"岗位不存在: {position_name}", http_status=404)
    return ok(data=detail)


@router.put("/positions/{position_name}")
async def update_position_definition(
    position_name: str,
    req: dict,
    current_user: dict = Depends(require_permission("admin:*")),
):
    """人工编辑岗位定义（§12.2），所有实际变更写入 PositionEditLog 节点。

    请求体（均可选，无字段时为空操作返回"无变更"）：
        skills: 技能列表全量替换，每项 {name, necessity: must|nice, weight: 0.0-1.0}
        core_duties / scenarios: 字符串数组，更新 Position 节点属性
    """
    skills = req.get("skills")
    core_duties = req.get("core_duties")
    scenarios = req.get("scenarios")
    err = validate_position_edit(skills, core_duties, scenarios)
    if err:
        return error(ERR_VALIDATION, err)

    editor_id = current_user.get("sub") or current_user.get("user_id", "admin")
    result = await asyncio.to_thread(
        _edit_position_neo4j, position_name, editor_id, skills, core_duties, scenarios
    )
    if not result["exists"]:
        return error(ERR_NOT_FOUND, f"岗位不存在: {position_name}", http_status=404)
    # 编辑已生效：失效岗位详情缓存（graph.py key 为 graph:position:{id}:{scope}，
    # all=全量可见，public=公开态），避免用户读到 5min 旧数据
    if result["id"]:
        await redis_client.delete(f"graph:position:{result['id']}:all")
        await redis_client.delete(f"graph:position:{result['id']}:public")
    return ok(
        data={
            "position_name": position_name,
            "updated": result["updated"],
            "diff_summary": result["diff_summary"],
        },
        msg="无变更" if not result["updated"] else "已保存编辑",
    )


# ============================================================
# LLM provider 配置（持久化到 llm_providers.yaml）
# ============================================================

_LLM_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "llm_providers.yaml"


def mask_secret(value: str) -> str:
    """密钥打码：保留后 4 位，其余掩码；空值返回空串。"""
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


def mask_providers(providers: list[dict]) -> list[dict]:
    """对 provider 列表的 api_key 打码（不修改入参）。"""
    return [{**p, "api_key": mask_secret(str(p.get("api_key") or ""))} for p in providers]


def validate_providers(providers: list) -> str | None:
    """校验 provider 列表，返回错误信息或 None。

    约束：非空列表；name 唯一且为安全字符；base_url 为 http(s) 地址；
    model 非空；priority 正整数且唯一；enabled 布尔。
    """
    if not isinstance(providers, list) or not providers:
        return "providers 必须是非空列表"
    seen_names: set[str] = set()
    seen_priorities: set[int] = set()
    for i, p in enumerate(providers):
        if not isinstance(p, dict):
            return f"第 {i + 1} 个 provider 必须是对象"
        name = (p.get("name") or "").strip()
        base_url = (p.get("base_url") or "").strip()
        model = (p.get("model") or "").strip()
        if not name:
            return f"第 {i + 1} 个 provider 缺少 name"
        if not re.match(r"^[A-Za-z0-9_-]+$", name):
            return f"name '{name}' 只能包含字母/数字/下划线/短横线"
        if name in seen_names:
            return f"name '{name}' 重复"
        seen_names.add(name)
        if not base_url.startswith(("http://", "https://")):
            return f"provider '{name}' 的 base_url 必须以 http(s):// 开头"
        if not model:
            return f"provider '{name}' 缺少 model"
        priority = p.get("priority")
        if not isinstance(priority, int) or priority < 1:
            return f"provider '{name}' 的 priority 必须为正整数"
        if priority in seen_priorities:
            return f"priority {priority} 重复（provider '{name}'）"
        seen_priorities.add(priority)
        if not isinstance(p.get("enabled", True), bool):
            return f"provider '{name}' 的 enabled 必须是布尔值"
    return None


def load_llm_config(path: Path) -> dict:
    """读取 yaml 配置。"""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_llm_config(path: Path, providers: list) -> dict:
    """校验并写回 yaml，返回写回后的完整配置。

    api_key 为空白或含掩码（*）时保持原值，明文才更新；
    写回保留原文件头部注释（到顶层键 providers 之前）。
    """
    err = validate_providers(providers)
    if err:
        raise ValueError(err)

    text = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    old = {
        p["name"]: p for p in data.get("providers", [])
        if isinstance(p, dict) and p.get("name")
    }

    clean = []
    for p in providers:
        name = (p.get("name") or "").strip()
        api_key = (p.get("api_key") or "").strip()
        if not api_key or "*" in api_key:
            api_key = (old.get(name) or {}).get("api_key", "")
        entry = {
            "name": name,
            "priority": int(p["priority"]),
            "base_url": (p.get("base_url") or "").strip(),
            "api_key": api_key,
            "model": (p.get("model") or "").strip(),
            "supports_function_calling": bool(p.get("supports_function_calling", True)),
            "enabled": bool(p.get("enabled", True)),
        }
        # provider 特定请求参数（如 deepseek 关闭思考模式 thinking.type=disabled），非 dict 忽略
        extra_body = p.get("extra_body")
        if isinstance(extra_body, dict) and extra_body:
            entry["extra_body"] = extra_body
        clean.append(entry)
    data["providers"] = clean

    # 保留原文件头部注释块（到顶层键 providers: 为止），rest 由 dump 生成
    parts = re.split(r"^providers:\s*$", text, maxsplit=1, flags=re.M)
    header = parts[0] if len(parts) == 2 else ""
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
    Path(path).write_text(header + body, encoding="utf-8")

    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


@router.get("/llm-config")
async def get_llm_config():
    """读取当前生效 LLM provider 配置（api_key 打码，不明文回显）。"""
    try:
        cfg = load_llm_config(_LLM_CONFIG_PATH)
    except (OSError, yaml.YAMLError):
        return error(ERR_INTERNAL, "LLM 配置读取失败")
    cfg["providers"] = mask_providers(cfg.get("providers", []))
    return ok(data=cfg)


@router.put("/llm-config")
async def update_llm_config(req: dict):
    """保存 LLM provider 配置（持久化到 yaml，api_key 留空/掩码保持原值）。"""
    providers = req.get("providers")
    try:
        saved = save_llm_config(_LLM_CONFIG_PATH, providers)
    except ValueError as e:
        return error(ERR_VALIDATION, str(e))
    except (OSError, yaml.YAMLError):
        return error(ERR_INTERNAL, "LLM 配置保存失败")
    saved["providers"] = mask_providers(saved.get("providers", []))
    return ok(data=saved)


# ============================================================
# 运行时配置（08-16：管理后台 /admin/settings 可编辑、重启生效）
# ============================================================

@router.get("/runtime-config")
async def get_runtime_config():
    """读取运行时配置（非敏感运行参数；rate_limit 返回各源生效值）。"""
    from app.core import runtime_config

    data = runtime_config.load_all()
    # rate_limit 展示"默认 + 覆盖"合并后的生效值（crawlers.settings 启动时已合并）
    try:
        from crawlers.settings import RATE_LIMIT as CRAWLER_RATE_LIMIT

        data["rate_limit"] = {
            src: {
                "req_per_min": cfg.get("req_per_min", 4),
                "delay_range": [int(cfg["delay_range"][0]), int(cfg["delay_range"][1])]
                if cfg.get("delay_range") else None,
            }
            for src, cfg in CRAWLER_RATE_LIMIT.items()
        }
    except Exception:
        pass  # 独立运行环境无 crawlers 包时仅返回文件内容
    return ok(data=data)


@router.put("/runtime-config")
async def update_runtime_config(req: dict):
    """校验并持久化运行时配置（runtime_settings.json，重启后生效）。"""
    from app.core import runtime_config

    try:
        data = runtime_config.save(req)
    except ValueError as e:
        return error(ERR_VALIDATION, str(e))
    except OSError:
        return error(ERR_INTERNAL, "配置保存失败，请检查目录权限")
    return ok(data=data)
