"""drop unused skill_freq_observation table

Revision ID: 20260823_001
Revises: 20260822_001
Create Date: 2026-08-23 20:00:00.000000

08-23 闭环收敛 P1-3：skill_freq_observation 建表后从未接线（无生产者/
无消费者——趋势计算实际走 graph_versions 快照路径）。按「单一事实源，
不保留两套口径」删除该死表；如未来需要结构化频次观测，应作为独立设计
（含生产者/消费者/版本对齐）重新评审，而非保留空壳 schema。

表自创建以来无任何写入（无生产者），drop 无数据损失。
"""

from alembic import op

revision = "20260823_001"
down_revision = "20260822_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index(
        "ix_skill_freq_observation_skill_obs", table_name="skill_freq_observation"
    )
    op.drop_table("skill_freq_observation")


def downgrade() -> None:
    # 恢复空壳表（原始建表迁移 20260822_001 的完整定义）
    import sqlalchemy as sa

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
