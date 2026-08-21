"""create dict_guard tables (dict_proposals / dict_change_logs)

Revision ID: 20260821_001
Revises: 20260820_002
Create Date: 2026-08-21 22:00:00.000000

技能字典自治守卫（dict-guard）：每日 LLM 评估图谱技能数据 → 分级调整字典过滤。
- dict_proposals：高风险提案进人工审批池（remove_stopword/protect_whitelist 一律、
  超影响面/低置信的 add_stopword）
- dict_change_logs：动态过滤层（skill_filters_dynamic.json）每次变更的审计链
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260821_001"
down_revision = "20260820_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dict_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("term", sa.String(length=100), nullable=False),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("llm_confidence", sa.Float(), nullable=True),
        sa.Column("evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("impact_stats", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("run_date", sa.String(length=10), nullable=False),
        sa.Column("reviewed_by", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("review_reason", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_dict_proposals_term", "dict_proposals", ["term"])
    op.create_index("ix_dict_proposals_status", "dict_proposals", ["status"])
    op.create_index("ix_dict_proposals_run_date", "dict_proposals", ["run_date"])

    op.create_table(
        "dict_change_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("term", sa.String(length=100), nullable=False),
        sa.Column("action", sa.String(length=30), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("proposal_id", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("impact_stats", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("applied_by", sa.String(length=64), nullable=False, server_default="system"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_dict_change_logs_term", "dict_change_logs", ["term"])


def downgrade() -> None:
    op.drop_index("ix_dict_change_logs_term", table_name="dict_change_logs")
    op.drop_table("dict_change_logs")
    op.drop_index("ix_dict_proposals_run_date", table_name="dict_proposals")
    op.drop_index("ix_dict_proposals_status", table_name="dict_proposals")
    op.drop_index("ix_dict_proposals_term", table_name="dict_proposals")
    op.drop_table("dict_proposals")
