"""add user profile fields

Revision ID: 20260802_001
Revises: 20260801_002
Create Date: 2026-08-02 21:00:00.000000

FE-M4-04 个人中心：users 表增加 email / phone / bio 个人资料字段（默认空串）。
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260802_001"
down_revision = "20260801_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email", sa.String(255), nullable=False, server_default=""))
    op.add_column("users", sa.Column("phone", sa.String(50), nullable=False, server_default=""))
    op.add_column("users", sa.Column("bio", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("users", "bio")
    op.drop_column("users", "phone")
    op.drop_column("users", "email")
