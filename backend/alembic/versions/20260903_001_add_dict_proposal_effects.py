"""dict_proposals 副作用执行态列（非原子性处理）

Revision ID: 20260903_001
Revises: 20260902_001
Create Date: 2026-09-03 21:00:00.000000

背景：dict-guard 人工审批的副作用（动态词表 / Neo4j 清理）跨 PG/Neo4j/Redis
无分布式事务，可能"PG 已 approved 但副作用失败"。补充持久化标记供每日巡检
幂等重试，使对账可落地：
- effects_applied：None=未进入 approve 副作用 / True=已生效 / False=失败待重试
- effects_error：副作用失败的错误摘要（重试成功时清空）
- effects_retry_count：副作用重试次数（上限后交人工）
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260903_001"
down_revision = "20260902_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dict_proposals",
        sa.Column("effects_applied", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "dict_proposals",
        sa.Column("effects_error", sa.String(length=1000), nullable=False, server_default=""),
    )
    op.add_column(
        "dict_proposals",
        sa.Column("effects_retry_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("dict_proposals", "effects_retry_count")
    op.drop_column("dict_proposals", "effects_error")
    op.drop_column("dict_proposals", "effects_applied")