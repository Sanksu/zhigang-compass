"""业务 Pydantic Schema（请求/响应模型）。"""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── 用户 ──

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=6, max_length=256)


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=6, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 1800


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class UserProfile(BaseModel):
    id: str
    username: str
    role: str
    is_active: bool
    created_at: datetime


# ── 审计日志 ──

class AuditLogCreate(BaseModel):
    user_id: str
    action: str = Field(..., max_length=100)
    resource: str = Field(..., max_length=100)
    resource_id: Optional[str] = None
    detail: dict = Field(default_factory=dict)
    ip_address: str = ""


class AuditLogResponse(BaseModel):
    id: int
    user_id: str
    action: str
    resource: str
    resource_id: Optional[str]
    detail: Any
    ip_address: str
    created_at: datetime


# ── 任务状态 ──

class TaskStatusCreate(BaseModel):
    task_type: str = Field(..., max_length=50)


class TaskStatusUpdate(BaseModel):
    status: Optional[str] = None
    progress: Optional[float] = None
    result: Optional[dict] = None
    error: Optional[str] = None


class TaskStatusResponse(BaseModel):
    id: str
    task_type: str
    status: str
    progress: float
    result: Any
    error: str
    created_at: datetime
    updated_at: datetime


# ── 简历缓存 ──

class ResumeCacheResponse(BaseModel):
    id: str
    file_hash: str
    file_name: str
    parsed_data: Any
    version: int
    created_at: datetime
    updated_at: datetime