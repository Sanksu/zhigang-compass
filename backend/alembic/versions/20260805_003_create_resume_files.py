"""create resume_files table

Revision ID: 20260805_003
Revises: 20260805_002
Create Date: 2026-08-05 16:00:00.000000

§8.1 文件解析：简历原始文件 DB 留存（content 字节存 PostgreSQL），
仅上传者本人可下载，管理员无权访问原文。删除简历时联动删除本表行。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260805_003"
down_revision = "20260805_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "resume_files",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("resume_id", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False, server_default=""),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_resume_files"),
    )
    op.create_index("ix_resume_files_resume_id", "resume_files", ["resume_id"])
    op.create_index("ix_resume_files_user_id", "resume_files", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_resume_files_user_id", table_name="resume_files")
    op.drop_index("ix_resume_files_resume_id", table_name="resume_files")
    op.drop_table("resume_files")
