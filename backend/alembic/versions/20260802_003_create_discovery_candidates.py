"""create discovery_candidates table

Revision ID: 20260802_003
Revises: 20260802_002
Create Date: 2026-08-02 23:00:00.000000

AL-M4-01 新岗位发现候选池：discovery_candidates 表，
存储门控命中 + RAG 接地后的 candidate 供 admin 审核流转。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260802_003"
down_revision = "20260802_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "discovery_candidates",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("position_name", sa.String(150), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="candidate"),
        sa.Column("features", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("confidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("evidence_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("seed_matched", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("rag_matched", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("definition_draft", sa.Text(), nullable=False, server_default=""),
        sa.Column("detected_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("position_name", name="uq_discovery_candidates_position_name"),
    )
    op.create_index("ix_discovery_candidates_position_name", "discovery_candidates", ["position_name"])


def downgrade() -> None:
    op.drop_index("ix_discovery_candidates_position_name", table_name="discovery_candidates")
    op.drop_table("discovery_candidates")
