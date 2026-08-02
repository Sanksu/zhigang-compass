"""业务表 ORM 模型。

包含：
- users：系统用户（JWT 认证主体）
- audit_logs：审计日志（≥180 天保留，BE-M4-03）
- task_status：异步任务状态（简历解析、LLM 抽取等）
- resume_cache：简历解析缓存（避免重复解析）
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class User(Base):
    """系统用户。"""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    username: Mapped[str] = mapped_column(
        String(100), unique=True, index=True, nullable=False
    )
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="guest")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # 个人资料（FE-M4-04 个人中心：默认空串，PUT /auth/me 更新）
    email: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    phone: Mapped[str] = mapped_column(String(50), default="", nullable=False)
    bio: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class AuditLog(Base):
    """审计日志。"""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id"), index=True, nullable=False
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(100), nullable=True)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    ip_address: Mapped[str] = mapped_column(String(45), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TaskStatus(Base):
    """异步任务状态追踪。"""

    __tablename__ = "task_status"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    task_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending | running | success | failed
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ResumeCache(Base):
    """简历解析缓存。"""

    __tablename__ = "resume_cache"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    file_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    parsed_data: Mapped[dict] = mapped_column(JSONB, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class GraphVersion(Base):
    """图谱版本快照（设计文档 7.1 版本管理）。

    snapshot_json 为 APOC 全量快照 {nodes, edges}，用于版本 Diff 与技能趋势回溯。
    版本保留 90 天，每日 05:00 前发布 T+1 版本。
    """

    __tablename__ = "graph_versions"

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True  # 版本号，如 graph_v20260801
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    change_summary: Mapped[str] = mapped_column(Text, default="")
    triggered_by: Mapped[str] = mapped_column(String(20), default="scheduled")
    snapshot_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    node_added: Mapped[int] = mapped_column(Integer, default=0)
    node_removed: Mapped[int] = mapped_column(Integer, default=0)
    node_changed: Mapped[int] = mapped_column(Integer, default=0)


class Occupation(Base):
    """权威岗位库（O*NET 标准职业分类，设计文档 5.1 Occupation 节点）。

    作为新岗位发现阶段二 RAG 接地（§7.2.3）的权威检索源：candidate 岗位名
    通过关键词/别名命中权威定义后，生成岗位定义草案进入 admin 审核队列。
    数据由 scripts/import_occupations.py 从 O*NET 官方 CSV 一次性导入。
    """

    __tablename__ = "occupations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(
        String(10), unique=True, index=True, nullable=False  # O*NET-SOC 代码，如 15-1252.00
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)  # O*NET-SOC 标题（英文）
    category: Mapped[str] = mapped_column(String(100), default="")  # SOC major group，如 Computer and Mathematical
    definition: Mapped[str] = mapped_column(Text, default="")  # 职业定义
    aliases: Mapped[list] = mapped_column(JSONB, default=list)  # 别名/俗称（Job Titles 聚合）
    source: Mapped[str] = mapped_column(String(20), default="onet")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class DiscoveryCandidate(Base):
    """新岗位发现候选池（设计文档 7.2.3 判定流程输出落库）。

    每日发现任务将门控命中 + RAG 接地后的 candidate 写入本表，
    admin 审核端点读取 pending 列表并执行 candidate→emerging/rejected 流转
    （状态同步到 Neo4j Position.status，见 state_machine.persist）。
    """

    __tablename__ = "discovery_candidates"

    id: Mapped[str] = mapped_column(
        String(40), primary_key=True  # candidate_id，如 cand-xxxx
    )
    position_name: Mapped[str] = mapped_column(
        String(150), unique=True, index=True, nullable=False
    )
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="candidate")
    features: Mapped[dict] = mapped_column(JSONB, default=dict)
    confidence: Mapped[dict] = mapped_column(JSONB, default=dict)
    evidence_refs: Mapped[list] = mapped_column(JSONB, default=list)
    seed_matched: Mapped[bool] = mapped_column(Boolean, default=False)
    rag_matched: Mapped[bool] = mapped_column(Boolean, default=False)
    definition_draft: Mapped[str] = mapped_column(Text, default="")
    detected_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )