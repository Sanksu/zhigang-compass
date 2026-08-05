"""create diagnosis_reports table

Revision ID: 20260805_001
Revises: 20260804_001
Create Date: 2026-08-05 10:00:00.000000

§6.4 RAG 检索增强：历史诊断报告落库，供通用 RAG 检索模块按岗位名
检索历史诊断上下文（evidence_id 形如 diagnosis:{match_id}）。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260805_001"
down_revision = "20260804_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "diagnosis_reports",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("match_id", sa.String(64), nullable=False),
        sa.Column("position_name", sa.String(150), nullable=False, server_default=""),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_diagnosis_reports"),
        sa.UniqueConstraint("match_id", name="uq_diagnosis_reports_match_id"),
    )
    op.create_index("ix_diagnosis_reports_match_id", "diagnosis_reports", ["match_id"])
    op.create_index("ix_diagnosis_reports_position_name", "diagnosis_reports", ["position_name"])


def downgrade() -> None:
    op.drop_index("ix_diagnosis_reports_position_name", table_name="diagnosis_reports")
    op.drop_index("ix_diagnosis_reports_match_id", table_name="diagnosis_reports")
    op.drop_table("diagnosis_reports")
