"""create match_results / match_feedback / rejected_changes tables

Revision ID: 20260805_002
Revises: 20260805_001
Create Date: 2026-08-05 14:00:00.000000

§11.4.1 数据模型落库：匹配结果/匹配反馈/审核驳回变更三表 PostgreSQL 持久化
（Redis 仍为匹配结果与反馈的主存储，本批表为契约要求的关系型副本）。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260805_002"
down_revision = "20260805_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "match_results",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("match_id", sa.String(64), nullable=False),
        sa.Column("position_name", sa.String(150), nullable=False, server_default=""),
        sa.Column("user_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_match_results"),
        sa.UniqueConstraint("match_id", name="uq_match_results_match_id"),
    )
    op.create_index("ix_match_results_match_id", "match_results", ["match_id"])

    op.create_table(
        "match_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("match_id", sa.String(64), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("comment", sa.String(200), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_match_feedback"),
    )
    op.create_index("ix_match_feedback_match_id", "match_feedback", ["match_id"])

    op.create_table(
        "rejected_changes",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("position_name", sa.String(150), nullable=False),
        sa.Column("change_type", sa.String(50), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False, server_default=""),
        sa.Column("source", sa.String(50), nullable=False, server_default=""),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_rejected_changes"),
    )


def downgrade() -> None:
    op.drop_table("rejected_changes")
    op.drop_index("ix_match_feedback_match_id", table_name="match_feedback")
    op.drop_table("match_feedback")
    op.drop_index("ix_match_results_match_id", table_name="match_results")
    op.drop_table("match_results")
