"""create raw tables

Revision ID: 20260729_001
Revises:
Create Date: 2026-07-29 20:00:00.000000

4 张 raw 表：jd_raw / course_raw / paper_raw / community_raw
对应爬虫的 4 种 Item 类型，作为 Neo4j 图谱的数据源。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260729_001"
down_revision = None
branch_labels = None
depends_on = None


def _raw_columns():
    """raw 表共享列定义。"""
    return [
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("source_id", sa.String(200), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("crawled_at", sa.String(40), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("raw_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_desensitized", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    # jd_raw
    op.create_table(
        "jd_raw",
        *_raw_columns(),
        sa.UniqueConstraint("source", "source_id", name="uq_jd_raw_source_id"),
        sa.UniqueConstraint("fingerprint", name="uq_jd_raw_fingerprint"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jd_raw_source", "jd_raw", ["source"])
    op.create_index("ix_jd_raw_fingerprint", "jd_raw", ["fingerprint"])

    # course_raw
    op.create_table(
        "course_raw",
        *_raw_columns(),
        sa.UniqueConstraint("source", "source_id", name="uq_course_raw_source_id"),
        sa.UniqueConstraint("fingerprint", name="uq_course_raw_fingerprint"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_course_raw_source", "course_raw", ["source"])
    op.create_index("ix_course_raw_fingerprint", "course_raw", ["fingerprint"])

    # paper_raw
    op.create_table(
        "paper_raw",
        *_raw_columns(),
        sa.UniqueConstraint("source", "source_id", name="uq_paper_raw_source_id"),
        sa.UniqueConstraint("fingerprint", name="uq_paper_raw_fingerprint"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_paper_raw_source", "paper_raw", ["source"])
    op.create_index("ix_paper_raw_fingerprint", "paper_raw", ["fingerprint"])

    # community_raw
    op.create_table(
        "community_raw",
        *_raw_columns(),
        sa.UniqueConstraint("source", "source_id", name="uq_community_raw_source_id"),
        sa.UniqueConstraint("fingerprint", name="uq_community_raw_fingerprint"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_community_raw_source", "community_raw", ["source"])
    op.create_index("ix_community_raw_fingerprint", "community_raw", ["fingerprint"])


def downgrade() -> None:
    op.drop_index("ix_community_raw_fingerprint", table_name="community_raw")
    op.drop_index("ix_community_raw_source", table_name="community_raw")
    op.drop_table("community_raw")

    op.drop_index("ix_paper_raw_fingerprint", table_name="paper_raw")
    op.drop_index("ix_paper_raw_source", table_name="paper_raw")
    op.drop_table("paper_raw")

    op.drop_index("ix_course_raw_fingerprint", table_name="course_raw")
    op.drop_index("ix_course_raw_source", table_name="course_raw")
    op.drop_table("course_raw")

    op.drop_index("ix_jd_raw_fingerprint", table_name="jd_raw")
    op.drop_index("ix_jd_raw_source", table_name="jd_raw")
    op.drop_table("jd_raw")
