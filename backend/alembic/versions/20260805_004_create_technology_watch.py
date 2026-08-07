"""create technology_watch table

Revision ID: 20260805_004
Revises: 20260805_003
Create Date: 2026-08-05 18:00:00.000000

§7.2.5 趋势监测：技术热点观察池。学术/社区/课程源信号汇总（admin 周报可见，
不独立触发 candidate），JD 源命中阈值时自动提升 candidate。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260805_004"
down_revision = "20260805_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "technology_watch",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("skill_name", sa.String(128), nullable=False),
        sa.Column("signal_source", sa.String(16), nullable=False),
        sa.Column("signal_value", sa.Float(), nullable=False),
        sa.Column("period", sa.String(16), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="watch"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_signal_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_technology_watch"),
        sa.UniqueConstraint(
            "skill_name", "signal_source", "period", name="uq_technology_watch_skill_period"
        ),
    )
    op.create_index("ix_technology_watch_skill_name", "technology_watch", ["skill_name"])


def downgrade() -> None:
    op.drop_index("ix_technology_watch_skill_name", table_name="technology_watch")
    op.drop_table("technology_watch")
