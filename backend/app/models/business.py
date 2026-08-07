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
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector

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
    """权威岗位库三源（O*NET / 人社部大典 / LinkedIn，设计文档 5.1 Occupation 节点）。

    作为新岗位发现阶段二 RAG 接地（§7.2.3）与通用 RAG 检索（§6.4）的权威检索源：
    candidate 岗位名通过关键词/别名命中权威定义后，生成岗位定义草案进入 admin 审核队列。
    数据由 scripts/import_occupations.py 导入，source 字段区分来源（onet/hrss/linkedin），
    一次检索天然覆盖全部来源。
    """

    __tablename__ = "occupations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(
        String(10), unique=True, index=True, nullable=False  # O*NET-SOC / 大典 / LI 编号
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)  # 岗位标题（O*NET 英文，其余中文）
    category: Mapped[str] = mapped_column(String(100), default="")  # SOC major group / 大典中类
    definition: Mapped[str] = mapped_column(Text, default="")  # 职业定义
    aliases: Mapped[list] = mapped_column(JSONB, default=list)  # 别名/俗称（Job Titles 聚合）
    source: Mapped[str] = mapped_column(String(20), default="onet")  # onet | hrss | linkedin
    # 语义向量（Sentence-BERT 384 维，T-06 RAG 接地双路之 pgvector 语义检索；
    # 由 scripts/import_occupations.py 生成，模型不可用时为 NULL，语义路降级）
    embedding: Mapped[list | None] = mapped_column(Vector(384), nullable=True)
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


class DiagnosisReportRecord(Base):
    """历史诊断报告（设计文档 §6.4 RAG 检索源之一 / §9.5）。

    诊断报告生成后落库，供通用 RAG 检索模块按岗位名检索历史诊断上下文
    （evidence_id 形如 diagnosis:{match_id}）。match_id 唯一，重复生成幂等更新。
    """

    __tablename__ = "diagnosis_reports"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    match_id: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    position_name: Mapped[str] = mapped_column(
        String(150), default="", index=True, nullable=False
    )
    report: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class MatchResultRecord(Base):
    """匹配结果落库（设计文档 §11.4.1 match_results）。

    Redis（键 match:result:{match_id}，TTL 24h）为主存储，本表为契约要求的
    PostgreSQL 持久化副本：match_id 唯一，重复生成幂等更新（与诊断报告口径一致）。
    """

    __tablename__ = "match_results"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    match_id: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    position_name: Mapped[str] = mapped_column(String(150), default="", nullable=False)
    user_id: Mapped[str] = mapped_column(
        String(64), default="", nullable=False  # 当前请求用户（JWT sub）
    )
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class MatchFeedbackRecord(Base):
    """匹配反馈落库（设计文档 §11.4.1 match_feedback）。

    Redis List（键 match:feedback:{match_id}，保留 90 天）为主存储，本表追加
    同一反馈记录，供后续匹配效果评估（§9.6 用户反馈率指标）。
    """

    __tablename__ = "match_feedback"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    match_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)  # 1=👍 / -1=👎
    comment: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RejectedChange(Base):
    """审核驳回变更记录（设计文档 §11.4.1 rejected_changes）。

    记录 discovery/evolution 审核的人工否决（candidate→rejected 等），
    驳回可追溯，供后续审核质量复盘。
    """

    __tablename__ = "rejected_changes"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    position_name: Mapped[str] = mapped_column(String(150), nullable=False)
    change_type: Mapped[str] = mapped_column(
        String(50), nullable=False  # discovery_reject / evolution_reject
    )
    reason: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    source: Mapped[str] = mapped_column(String(50), default="", nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ResumeFile(Base):
    """简历原始文件留存（设计文档 §8.1 文件解析）。

    原始文件字节存 PostgreSQL（content 列），仅上传者本人可下载，
    管理员无权访问原文。上传写行、本人下载、删除简历时联动删除。
    resume_cache 表无 user_id，所有者归属记录在本表。
    """

    __tablename__ = "resume_files"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    resume_id: Mapped[str] = mapped_column(
        String(64), index=True, nullable=False  # 对应 resume_cache.id（解析任务 id）
    )
    user_id: Mapped[str] = mapped_column(
        String(64), index=True, nullable=False  # 上传者（JWT sub），下载鉴权用
    )
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(
        String(100), default="", nullable=False
    )
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TechnologyWatch(Base):
    """技术热点观察池（设计文档 §7.2.5 趋势监测）。

    学术/社区源（arXiv/GitHub/SO）与课程源的技术热点信号汇总，admin 周报
    可见，不独立触发 candidate。JD 源命中阈值时自动提升为 candidate
    （status=candidate_promoted，写入 discovery_candidates 交 admin 审核）。

    信号判定阈值（§7.2.5 条件监测矩阵）：
    - jd：3 月移动平均环比增长率 > 50%
    - arxiv/github/community：周频次超过历史均值 2σ
    - course：新增课程技能频次 2σ
    """

    __tablename__ = "technology_watch"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    skill_name: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    signal_source: Mapped[str] = mapped_column(
        String(16), nullable=False  # jd / arxiv / github / course / community
    )
    signal_value: Mapped[float] = mapped_column(Float, nullable=False)  # 2σ 偏离或环比增长率
    period: Mapped[str] = mapped_column(
        String(16), nullable=False  # 统计周期 YYYY-MM-DD
    )
    status: Mapped[str] = mapped_column(
        String(24), default="watch", nullable=False
    )  # watch / candidate_promoted / archived
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_signal_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        # 幂等 upsert 约束：同一技能同源同周期仅一行
        UniqueConstraint("skill_name", "signal_source", "period", name="uq_technology_watch_skill_period"),
    )