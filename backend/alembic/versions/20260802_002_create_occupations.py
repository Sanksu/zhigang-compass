"""create occupations table

Revision ID: 20260802_002
Revises: 20260802_001
Create Date: 2026-08-02 22:00:00.000000

AL-M4-01 权威岗位库：occupations 表（O*NET 标准职业分类），
供新岗位发现阶段二 RAG 接地检索。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260802_002"
down_revision = "20260802_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "occupations",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("code", sa.String(10), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("category", sa.String(100), nullable=False, server_default=""),
        sa.Column("definition", sa.Text(), nullable=False, server_default=""),
        sa.Column("aliases", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("source", sa.String(20), nullable=False, server_default="onet"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("code", name="uq_occupations_code"),
    )
    op.create_index("ix_occupations_code", "occupations", ["code"])


def downgrade() -> None:
    op.drop_index("ix_occupations_code", table_name="occupations")
    op.drop_table("occupations")
