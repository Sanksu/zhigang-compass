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
    Index,
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
    # 机制补强 ①：样本量对比告警（evidence 量比上版本萎缩<50%/膨胀>200% 时非空 JSONB）
    data_warning: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True, default=None,
        comment="样本量对比告警（机制补强①，阈值 50%/200%）",
    )


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


class SkillEmbedding(Base):
    """技能语义向量（设计文档 §11.4.3 skill_embeddings，IVFFLAT + Cosine）。

    id 取 Neo4j skill_id（字符串），embedding 由 scripts/backfill_embeddings.py
    生成（Sentence-BERT MiniLM 384 维），metadata 存技能名。skill/similar
    端点 pgvector 语义检索（§5.3 预留的演进落地）。
    """

    __tablename__ = "skill_embeddings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # Neo4j skill_id
    embedding: Mapped[list] = mapped_column(Vector(384), nullable=False)
    # 数据库列名 metadata（SQLAlchemy Declarative 保留属性名，ORM 侧用 payload）
    payload: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ProjectEmbedding(Base):
    """项目语义向量（设计文档 §11.4.3 project_embeddings，IVFFLAT + Cosine）。

    简历项目文本（name + 描述）向量，供人岗匹配 project_embeddings 比对
    （engine._project_score 优先使用预计算向量，缺失时回退 SBERT 文本相似度）。
    metadata: {"resume_id", "project_index", "project_name", "description", "text"}
    """

    __tablename__ = "project_embeddings"
    __table_args__ = (
        UniqueConstraint("resume_id", "project_index", name="uq_project_embeddings_resume_project"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    embedding: Mapped[list] = mapped_column(Vector(384), nullable=False)
    payload: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    resume_id: Mapped[str] = mapped_column(String(64), nullable=False)
    project_index: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class JdEmbedding(Base):
    """JD 语义向量（设计文档 §11.4.3 jd_embeddings，HNSW + Cosine）。

    JD 标题+公司+城市文本向量，dedup_simhash 语义去重辅助（HNSW 支持高
    吞吐近似检索）。metadata: {"jd_id", "title", "company", "city"}
    """

    __tablename__ = "jd_embeddings"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    embedding: Mapped[list] = mapped_column(Vector(384), nullable=False)
    payload: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    jd_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
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


class EvolutionEvent(Base):
    """岗位演化事件（机制补强②：born/merged/ended 可展示事实，PR #334 确认）。

    Neo4j EVOLVED_FROM 边（rename/split）保持不变；本表承载"岗位诞生/归并/消亡"
    三类可答辩事件的 PG 落库，供 /evolution/events 查询与前端事件流。
    """

    __tablename__ = "evolution_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version_id: Mapped[str] = mapped_column(
        String(64), index=True, nullable=False  # 归属版本，如 graph_v20260820
    )
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)  # born / merged / ended
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    from_name: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)  # 旧名（ended/merged）
    to_name: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)  # 新名（born/merged）
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)  # 附加（如 merged 的多个 from_names）


class DictProposal(Base):
    """技能字典调整提案（dict-guard 每日评估产出，人工审批通道）。

    分级自动策略（技能字典自治守卫方案 §4）：仅低风险 add_stopword
    （过硬门禁 + 影响面 ≤ 阈值 + LLM 置信度达标）自动生效并落 DictChangeLog，
    不建本表行；remove_stopword / protect_whitelist / 超影响面 / 低置信度
    一律进本表 pending，admin 审批后生效——字典是幻觉防控第三道防线载体，
    高风险变更不自动写（对齐岗位名 LLM 审查方案"不自动写规则库"原则）。
    """

    __tablename__ = "dict_proposals"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    term: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    entity_type: Mapped[str] = mapped_column(
        String(20), default="skill", index=True, nullable=False
        # 治理对象类型: skill（技能字典）/ position（岗位节点清理）/ course（课程脏边或孤立课程节点）
    )
    action: Mapped[str] = mapped_column(
        String(30), nullable=False  # skill: add/remove_stopword/protect_whitelist；position/course: remove_node/remove_edge
    )
    status: Mapped[str] = mapped_column(
        String(20), default="pending", index=True, nullable=False  # pending / approved / rejected
    )
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)  # LLM 判定理由
    llm_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence: Mapped[list] = mapped_column(JSONB, default=list)  # JD 样例/命中统计
    impact_stats: Mapped[dict] = mapped_column(JSONB, default=dict)  # {graph_nodes, jd_snapshots}
    run_date: Mapped[str] = mapped_column(String(10), index=True, nullable=False)  # 评估批次日期
    reviewed_by: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    review_reason: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DictChangeLog(Base):
    """字典变更审计（动态过滤层每次变更一行：自动生效/人工审批/回滚）。

    与 configs/skill_filters_dynamic.json 一一对应：JSON 文件是运行时生效态，
    本表是完整审计链（谁/何时/何词/何动作/何理由/影响面），回滚依据。
    """

    __tablename__ = "dict_change_logs"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    term: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    entity_type: Mapped[str] = mapped_column(
        String(20), default="skill", index=True, nullable=False
        # 与 DictProposal.entity_type 同语义；skill=动态层变更，position/course=图谱清理
    )
    action: Mapped[str] = mapped_column(String(30), nullable=False)  # 同 DictProposal.action + rollback
    source: Mapped[str] = mapped_column(
        String(20), nullable=False  # auto（守卫自动）/ manual（人工审批）/ rollback
    )
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False  # skill=blocked/protected；position/course=node/edge
    )
    proposal_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 关联提案
    reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)  # {operator, evidence 摘要等}
    impact_stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    applied_by: Mapped[str] = mapped_column(String(64), default="system", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LLMDecisionRecord(Base):
    """LLM 语义决策流水——六域统一决策信封（主仓灰度底座）。

    覆盖 jd_extract / position_normalize / skill_normalize / position_classify /
    cluster_label / skill_classify / governance / skill_relation 的每次 LLM
    结构化决策，一行一条：输入哈希、证据引用、provider/model、prompt/schema
    版本、结构化输出、风险路由结果、审核与生效状态、回滚引用。

    设计约束：
    - 不复制完整敏感原文，只用实体引用、证据摘要与输入/输出哈希；
    - status 状态机：shadow（只记录）→ proposal（进审核池）→ approved /
      rejected；低风险可 auto_applied；不变量失败记 blocked；超预算记 failed；
    - risk_tier 由 app.services.llm_decision.risk_tier_for 统一裁决（R0/R1/R2）。
    """

    __tablename__ = "llm_decision_records"
    __table_args__ = (
        Index("ix_llm_decision_domain_status", "domain", "status"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    domain: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(30), default="", nullable=False)
    entity_id: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    run_id: Mapped[str] = mapped_column(String(40), default="", nullable=False, index=True)
    env: Mapped[str] = mapped_column(String(16), default="production", nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSONB, default=list)
    provider: Mapped[str] = mapped_column(String(50), default="", nullable=False)
    model: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    structured_output: Mapped[dict] = mapped_column(JSONB, default=dict)
    postprocessed_output: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=None)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    gate_result: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    risk_tier: Mapped[str] = mapped_column(String(8), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="shadow", nullable=False)
    reviewer: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    review_reason: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    effects_applied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rollback_ref: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    fallback_reason: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SkillDynamicRelation(Base):
    """LLM 技能关系审批执行通道（PR9b）——proposal→approved 的关系持久化。

    与 configs YAML 静态种子并列的第二个图关系事实源：管理员在决策页
    approve 一条 skill_relation proposal 后，此处落一行（PG），由
    scripts/sync_dynamic_relations.py 与 YAML 种子共同幂等 MERGE 入图。

    - ALTERNATIVE_OF 对称语义一条记录，同步时双向 MERGE
    - source=llm_review（人工审批 + LLM 提议链路），与 YAML 的 manual 区分
    - applied_to_graph 标记同步进度（幂等不依赖）
    """

    __tablename__ = "skill_dynamic_relations"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    source_skill: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    target_skill: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    relation_type: Mapped[str] = mapped_column(
        String(24), nullable=False  # PREREQUISITE_OF | BELONGS_TO | ALTERNATIVE_OF
    )
    direction: Mapped[str] = mapped_column(String(12), default="a_to_b", nullable=False)
    proposal_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(24), default="llm_review", nullable=False)
    reviewed_by: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    review_reason: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    applied_to_graph: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NameNormalizationRequest(Base):
    """名称归一审批执行通道（PR3 c：position/skill normalize proposal→approved 的持久化）。

    与 skill_dynamic_relations 并列的第二个图幂等变更事实源：管理员在决策页
    approve 一条 position_normalize / skill_normalize proposal 后，此处落一行（PG），
    由 scripts/sync_dynamic_normalization.py 幂等应用到 Neo4j（rename/merge）。
    图写入不在 API 端点内发生（对齐 skill_relation 的「approve 写 PG、独立 sync
    脚本写图」原则）。

    语义：
    - entity_type=skill：source_name 技能节点并入/改名为 target_name（target 必须为
      权威标准名，hard gate 保证）。action=merge（技能归并到标准名）。
    - entity_type=position：source_name 岗位节点并入/改名为 target_name。
      action=merge 或 rename 视决策意图。

    幂等：sync 脚本用 MERGE / SET（重复执行安全），与 applied_to_graph 标记无关
    （失败可重跑）。sync 侧按图形态自纠正：目标名节点已存在 → merge（重连边、
    合并 freq、删除源节点）；不存在 → rename（SET source.name=target）。
    action 字段为审批时的意图记录（审计用），sync 实际操作以目标节点存在性为准。
    """

    __tablename__ = "name_normalization_requests"
    __table_args__ = (
        # 幂等去重：同一 proposal 仅一行
        UniqueConstraint("proposal_id", name="uq_name_normalization_proposal"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    entity_type: Mapped[str] = mapped_column(
        String(20), nullable=False  # skill | position
    )
    action: Mapped[str] = mapped_column(
        String(16), nullable=False  # rename | merge
    )
    source_name: Mapped[str] = mapped_column(String(128), nullable=False)
    target_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # rename 后保留名=target_name；merge 后主节点名=target_name（primary_node_name 冗余
    # 供 sync 侧判断与做证据，避免再读图）
    primary_node_name: Mapped[str] = mapped_column(String(128), nullable=False)
    proposal_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(24), default="llm_review", nullable=False)
    reviewed_by: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    review_reason: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    applied_to_graph: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SkillCategoryApproval(Base):
    """技能分类审批执行通道（PR 补：skill_classify shadow → approved 的持久化）。

    技能分类 worker 只写 `suggested_category*` 提议字段（不动权威 category），
    LLM 建议落 llm_decision_records（domain=skill_classify、status=shadow、
    risk_tier=R0）。管理员在决策页 approve 后此处落一行（PG），由
    scripts/sync_dynamic_categories.py 把 Skill.category 晋升为批准值。
    reject 仅流转决策状态无副作用（与 skill_relation 一致）。

    幂等：sync 脚本用 SET（重复执行安全），与 applied_to_graph 标记无关。
    category 必须命中权威分类枚举（KNOWN_CATEGORIES，classify_skill 侧保证）。
    """

    __tablename__ = "skill_category_approvals"
    __table_args__ = (
        # 幂等去重：同一 proposal 仅一行
        UniqueConstraint("proposal_id", name="uq_skill_category_proposal"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    skill_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    proposal_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(24), default="llm_review", nullable=False)
    reviewed_by: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    review_reason: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    applied_to_graph: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SkillAlias(Base):
    """技能别名回写记录（方案①：LLM 发现别名 → 人工审批 → 回写词典）。

    与 SkillCategoryApproval 单表晋升范式一致：一条记录既是审批事实
    （variant→standard_name，proposal_id 去重），也是运行时归一化并查源
    （normalize_skill 只读 status=approved 的行）。approve 后 status=approved；
    sync_dynamic_aliases 幂等读 approved 行写 Neo4j/图谱（可选）。

    semantic：variant 为 LLM 发现的"向量不相似但语义等价"的别名（缩写/中英/
    版本变体），standard_name 必须命中 known_standard_names()（gate 守护，
    防虚构标准名）。
    """

    __tablename__ = "skill_aliases"
    __table_args__ = (
        UniqueConstraint("variant", name="uq_skill_alias_variant"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4())
    )
    variant: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    standard_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # pending（LLM 建议待审）→ approved（人工批准，normalize 并查）→ rejected
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    proposal_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    source: Mapped[str] = mapped_column(String(24), default="llm_review", nullable=False)
    reviewed_by: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    review_reason: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    applied_to_graph: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
