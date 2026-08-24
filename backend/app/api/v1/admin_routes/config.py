"""管理后台配置域路由：LLM provider 配置 + 运行时配置（RBAC admin only）。

对齐契约 /api/v1/admin/llm-config 与 /runtime-config。LLM provider 持久化
到 configs/llm_providers.yaml（api_key 打码不回显，明文才更新，文件头注释
保留）；运行时配置持久化 runtime_settings.json，重启后生效。

安全设计（AGENTS.md §4.1 安全红线，变更须人工逐行审查）：
- PUT 请求体经 Pydantic 强类型校验（失败由全局 RequestValidationError
  处理器映射 422/4000，与统一错误码表一致）
- 写路径持进程内锁串行化（read-modify-write 竞态防护）；直写不改名，
  兼容单文件 bind mount 场景（os.replace 会 EBUSY）
- 每次保存写 AuditLog(admin.llm_config.update)，detail 仅含非敏感快照
  （name/priority/enabled/model/base_url），绝不落 api_key
"""

import re
import threading
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_permission
from app.core.database import get_db
from app.core.errors import ERR_INTERNAL, ERR_VALIDATION
from app.models.business import AuditLog
from app.schemas.common import error, ok

router = APIRouter()

# ============================================================
# LLM provider 配置（持久化到 llm_providers.yaml）
# ============================================================

_LLM_CONFIG_PATH = Path(__file__).resolve().parents[4] / "configs" / "llm_providers.yaml"

# read-modify-write 串行锁：admin PUT 并发保存同一 yaml 时防止互相覆盖
_CONFIG_WRITE_LOCK = threading.Lock()


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
        api_key_env = (p.get("api_key_env") or "").strip()
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
        if api_key_env and not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", api_key_env):
            return f"provider '{name}' 的 api_key_env 不是合法环境变量名"
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
    进程内锁串行化，防并发 PUT 的 read-modify-write 竞态互相覆盖。
    """
    err = validate_providers(providers)
    if err:
        raise ValueError(err)

    with _CONFIG_WRITE_LOCK:
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
            has_env = bool((p.get("api_key_env") or "").strip())
            if "*" in api_key or not api_key:
                # 逐行审查修复（2026-08-24）：掩码/留空时——配了 env 一律存空串
                # （掩码原样落盘会被 _resolve_api_key 当显式明文压过 env，有害）；
                # 未配 env 保持原值（既有语义）
                if has_env:
                    api_key = ""
                else:
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
            # 密钥环境变量名（推荐方式：key 经 env 注入不落盘），非空才写
            api_key_env = (p.get("api_key_env") or "").strip()
            if api_key_env:
                entry["api_key_env"] = api_key_env
            # provider 特定请求参数（如 deepseek 关闭思考模式 thinking.type=disabled），非 dict 忽略
            extra_body = p.get("extra_body")
            if isinstance(extra_body, dict) and extra_body:
                entry["extra_body"] = extra_body
            clean.append(entry)
        data["providers"] = clean

        # 保留原文件头部注释块（到顶层键 providers: 为止），rest 由 dump 生成。
        # 直写不改名：单文件 bind mount 下 os.replace 会 EBUSY（runtime 断点）
        parts = re.split(r"^providers:\s*$", text, maxsplit=1, flags=re.M)
        header = parts[0] if len(parts) == 2 else ""
        body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
        Path(path).write_text(header + body, encoding="utf-8")

        return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


class LlmProviderIn(BaseModel):
    """PUT /admin/llm-config 单个 provider（契约 LlmProviderConfig）。"""

    name: str = Field(description="provider 唯一标识")
    base_url: str = Field(description="OpenAI 兼容 API 地址")
    model: str = Field(min_length=1, description="模型名")
    priority: int = Field(ge=1, description="尝试优先级，1 最高，列表内唯一")
    enabled: bool = Field(default=True)
    supports_function_calling: bool = Field(default=True)
    api_key: str = Field(default="", description="留空或含掩码保持原值；明文才更新（推荐改用 api_key_env）")
    api_key_env: str = Field(default="", description="密钥环境变量名：key 经 env 注入不落盘（推荐）")
    extra_body: dict[str, Any] | None = Field(default=None)


class LlmConfigIn(BaseModel):
    """PUT /admin/llm-config 请求体。"""

    providers: list[LlmProviderIn] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_business_rules(self) -> "LlmConfigIn":
        # 业务规则单一事实源仍是 validate_providers（纯函数层同样把关），
        # 此处委托复用：违规 → ValidationError → 全局处理器映射 422/4000
        err = validate_providers([p.model_dump() for p in self.providers])
        if err:
            raise ValueError(err)
        return self


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
async def update_llm_config(
    req: LlmConfigIn,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(require_permission("admin:*")),
):
    """保存 LLM provider 配置（持久化到 yaml，api_key 留空/掩码保持原值）。

    请求体经 LlmConfigIn 强校验（422/4000）；保存成功写审计日志，
    detail 只含非敏感快照，绝不记录 api_key。
    """
    operator = current_user.get("sub") or current_user.get("user_id", "admin")
    try:
        saved = save_llm_config(
            _LLM_CONFIG_PATH,
            [p.model_dump(exclude_none=True) for p in req.providers],
        )
    except ValueError as e:
        return error(ERR_VALIDATION, str(e))
    except (OSError, yaml.YAMLError):
        return error(ERR_INTERNAL, "LLM 配置保存失败")

    # 审计留痕（ADMIN 类）：仅非敏感字段快照，api_key 永不入日志
    db.add(AuditLog(
        user_id=operator,
        action="admin.llm_config.update",
        resource="llm_providers",
        detail={
            "providers": [
                {
                    "name": p.name,
                    "priority": p.priority,
                    "enabled": p.enabled,
                    "model": p.model,
                    "base_url": p.base_url,
                    "key_updated": bool(p.api_key) and "*" not in p.api_key,
                }
                for p in req.providers
            ],
        },
    ))
    await db.commit()

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
