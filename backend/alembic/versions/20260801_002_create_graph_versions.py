"""create graph_versions table

Revision ID: 20260801_002
Revises: 20260731_001
Create Date: 2026-08-01 16:00:00.000000

图谱版本快照表：graph_versions（设计文档 7.1 版本管理）
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260801_002"
down_revision = "20260731_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── graph_versions ──
    op.create_table(
        "graph_versions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("triggered_by", sa.String(20), nullable=False, server_default="scheduled"),
        sa.Column("snapshot_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("node_added", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("node_removed", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("node_changed", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_table("graph_versions")
