"""add graph_versions.data_warning (sample-size anomaly)

Revision ID: 20260820_001
Revises: 20260806_001
Create Date: 2026-08-20 09:30:00.000000

机制补强 ①（PR #334 张恺天确认）：样本量对比告警——graph_versions 增 data_warning
JSONB（evidence 量：Position 岗位数 / REQUIRES 边数 比上一版本萎缩<50% 或膨胀>200%
时记录，防"采集量波动被误判为能力变化"）。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260820_001"
down_revision = "20260806_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "graph_versions",
        sa.Column(
            "data_warning",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="样本量对比告警（机制补强①，阈值 50%/200%）",
        ),
    )


def downgrade() -> None:
    op.drop_column("graph_versions", "data_warning")
