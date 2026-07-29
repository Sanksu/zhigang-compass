"""raw 表 ORM 模型：爬虫产出的原始数据落地。

4 张表对应 4 种 Item 类型：
- jd_raw       ← JobItem           → 经 LLM 抽取后入 Neo4j（Position/Skill/Evidence）
- course_raw   ← CourseItem        → 直接入 Neo4j（Course + LEARNABLE_VIA）
- paper_raw    ← PaperItem         → 技术热点观察池（不入图谱）
- community_raw ← CommunityTrendItem → 技术热点观察池（不入图谱）

设计：
- snapshot (JSONB) 存储完整 Item 字段，便于回溯与抽取服务消费
- source + source_id 唯一约束，支持 upsert 去重
- fingerprint 唯一约束，SHA256(source:source_id)，与 CleaningPipeline 一致
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class _RawMixin:
    """raw 表共享字段。"""

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(50), index=True)
    source_id: Mapped[str] = mapped_column(String(200))
    source_url: Mapped[str] = mapped_column(Text, default="")
    crawled_at: Mapped[str] = mapped_column(String(40))
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    is_desensitized: Mapped[bool] = mapped_column(Boolean, default=False)
    compliance_note: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class JDRaw(_RawMixin, Base):
    """招聘信息原始数据。"""

    __tablename__ = "jd_raw"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_jd_raw_source_id"),
    )


class CourseRaw(_RawMixin, Base):
    """课程信息原始数据。"""

    __tablename__ = "course_raw"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_course_raw_source_id"),
    )


class PaperRaw(_RawMixin, Base):
    """论文原始数据（技术热点观察池）。"""

    __tablename__ = "paper_raw"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_paper_raw_source_id"),
    )


class CommunityRaw(_RawMixin, Base):
    """社区趋势原始数据（技术热点观察池）。"""

    __tablename__ = "community_raw"
    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_community_raw_source_id"),
    )
