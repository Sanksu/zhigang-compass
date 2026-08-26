"""admin 路由请求体 Pydantic 模型（第六轮审查 P1-4：裸 dict 收敛）。

与 LLM 输出「Pydantic 强校验」同源的防线：此前 12 处 `req: dict` 手工校验，
字段缺失/类型错误语义分散、文本字段无 max_length（超长靠 DB 列兜底或 500）。
校验失败由全局 RequestValidationError 处理器统一转 422 + code 4000（main.py）。

注意：extra 字段默认忽略（前端携带冗余字段不报错）；runtime-config 为开放
键集（校验在 runtime_config.save），保持 dict 载体不加模型。
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _reject_blank(value: str) -> str:
    """理由类字段 strip 后非空白（防纯空格绕过 min_length）。"""
    stripped = value.strip()
    if not stripped:
        raise ValueError("不能为空白（纯空格）")
    return stripped


class AdminReviewActionRequest(BaseModel):
    """审核动作类请求（岗位审核 / dict-guard 提案审核）。"""
    action: Literal["approve", "reject"] = Field(description="审核动作")
    reason: str = Field(min_length=1, max_length=500, description="审核理由（必填）")

    _reason_not_blank = field_validator("reason")(_reject_blank)


class AdminReasonRequest(BaseModel):
    """仅理由类请求（岗位归档）。"""
    reason: str = Field(min_length=1, max_length=500, description="归档理由（必填）")

    _reason_not_blank = field_validator("reason")(_reject_blank)


class AdminEvolutionReviewRequest(BaseModel):
    """演化审核：reason 可选（缺省 'admin evolution review'），modified 为属性修订。"""
    action: Literal["approve", "reject"]
    reason: Optional[str] = Field(default=None, max_length=500)
    modified: Optional[dict] = Field(default=None, description="approve 时合并进候选池 features 的属性修订")


class LLMDecisionReviewRequest(BaseModel):
    """LLM 决策审批（approve/reject 共用）。"""
    review_reason: str = Field(min_length=1, max_length=500, description="审批理由（必填）")

    _reason_not_blank = field_validator("review_reason")(_reject_blank)


class CreateUserRequest(BaseModel):
    """管理员代建用户。"""
    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=6, max_length=256)
    role: Literal["admin", "user", "guest"] = "user"


class UpdateUserRequest(BaseModel):
    """更新用户角色/启用状态（部分更新：未传字段不动）。"""
    role: Optional[Literal["admin", "user", "guest"]] = None
    status: Optional[Literal["active", "disabled"]] = None


class PositionSkillEdit(BaseModel):
    """岗位技能项（人工编辑）。"""
    name: str = Field(min_length=1, max_length=100)
    necessity: Literal["must", "nice"]
    weight: float = Field(ge=0.0, le=1.0)


class AdminPositionEditRequest(BaseModel):
    """人工编辑岗位定义（均可选，全空为无变更空操作）。"""
    skills: Optional[list[PositionSkillEdit]] = None
    core_duties: Optional[list[str]] = None
    scenarios: Optional[list[str]] = None


class CrawlTriggerRequest(BaseModel):
    """触发爬取（platform 白名单在端点内校验，keyword 空=热度/最新）。"""
    platform: str = Field(min_length=1, max_length=50)
    keyword: str = Field(default="", max_length=200)
    city: str = Field(default="", max_length=50)


class RuntimeConfigUpdateRequest(BaseModel):
    """运行时配置更新：开放键集（extra=allow），逐键校验在 runtime_config.save。"""
    model_config = ConfigDict(extra="allow")
