"""add entity_type to dict_guard tables (skill/position/course)

Revision ID: 20260821_002
Revises: 20260821_001
Create Date: 2026-08-21 23:00:00.000000

dict-guard 治理对象从技能字典横向扩展到图谱岗位/课程节点清理：
- entity_type 区分治理对象（skill 默认，兼容存量行；position 岗位节点；
  course 课程脏边或孤立课程节点），提案与审计各加一列。
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260821_002"
down_revision = "20260821_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dict_proposals",
        sa.Column("entity_type", sa.String(length=20), nullable=False, server_default="skill"),
    )
    op.create_index("ix_dict_proposals_entity_type", "dict_proposals", ["entity_type"])

    op.add_column(
        "dict_change_logs",
        sa.Column("entity_type", sa.String(length=20), nullable=False, server_default="skill"),
    )
    op.create_index("ix_dict_change_logs_entity_type", "dict_change_logs", ["entity_type"])


def downgrade() -> None:
    op.drop_index("ix_dict_change_logs_entity_type", table_name="dict_change_logs")
    op.drop_column("dict_change_logs", "entity_type")
    op.drop_index("ix_dict_proposals_entity_type", table_name="dict_proposals")
    op.drop_column("dict_proposals", "entity_type")