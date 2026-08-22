"""create skill_freq_observation table

Revision ID: 20260822_001
Revises: 20260821_002
Create Date: 2026-08-22 20:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "20260822_001"
down_revision = "20260821_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_freq_observation",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("skill_id", sa.String(length=64), nullable=False),
        sa.Column("skill_name", sa.Text(), nullable=True),
        sa.Column("obs_date", sa.Date(), nullable=False),
        sa.Column("frequency", sa.Integer(), nullable=False),
        sa.Column("total_requires", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="crawl"),
        sa.Column("snapshot_version", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_skill_freq_observation_skill_obs",
        "skill_freq_observation",
        ["skill_id", "obs_date"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_skill_freq_observation_skill_obs", table_name="skill_freq_observation"
    )
    op.drop_table("skill_freq_observation")
