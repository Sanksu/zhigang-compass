"""create evolution_events table

Revision ID: 20260820_002
Revises: 20260820_001
Create Date: 2026-08-20 10:30:00.000000

机制补强 ②（PR #334 张恺天确认）：谱系事件 born/merged/ended 落库——
evolution_events（version_id / event_type / from_name / to_name / detail JSONB），
Neo4j EVOLVED_FROM 边（rename/split）保持不变，本表承载"岗位诞生/归并/消亡"可答辩事实。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260820_002"
down_revision = "20260820_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evolution_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("version_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("from_name", sa.Text(), nullable=True),
        sa.Column("to_name", sa.Text(), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
    )
    op.create_index("ix_evolution_events_version_id", "evolution_events", ["version_id"])


def downgrade() -> None:
    op.drop_index("ix_evolution_events_version_id", table_name="evolution_events")
    op.drop_table("evolution_events")
