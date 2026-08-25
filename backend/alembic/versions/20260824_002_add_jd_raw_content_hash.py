"""add jd_raw.content_hash (LLM 抽取输入内容指纹，重爬不重抽治理)

Revision ID: 20260824_002
Revises: 20260824_001
Create Date: 2026-08-24 18:30:00.000000

batch_extract 落库时写 sha256(拼装后 JD 正文)；重爬更新正文/标题后哈希
变化 → 触发同条重抽（08-24 决策配套：重爬不重抽的语义滞后治理）。
存量行为空串，首次过批时回填（只补指纹不重抽）；未抽取行不受影响。
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260824_002"
down_revision = "20260824_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jd_raw",
        sa.Column("content_hash", sa.String(length=64), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("jd_raw", "content_hash")