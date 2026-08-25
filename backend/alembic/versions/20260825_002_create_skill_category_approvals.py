"""create skill_category_approvals (技能分类审批执行通道)

Revision ID: 20260825_002
Revises: 20260825_001
Create Date: 2026-08-25 18:00:00.000000

skill_classify（技能分类）shadow → approved 后的持久化目标：管理员在决策页
approve 一条 skill_classify shadow 记录后，此处落一行（PG），由
scripts/sync_dynamic_categories.py 把 Skill.category 晋升为批准值（幂等 SET）。

语义：
- skill_name：图谱技能节点名
- category：批准后的权威分类（KNOWN_CATEGORIES 枚举内，classify_skill 侧保证）
- unique(proposal_id) 幂等去重（approve 预查拒绝重复批准）
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260825_002"
down_revision = "20260825_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_category_approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("skill_name", sa.String(length=128), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("proposal_id", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False, server_default="llm_review"),
        sa.Column("reviewed_by", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("review_reason", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("applied_to_graph", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_skill_category_approvals_skill_name",
        "skill_category_approvals",
        ["skill_name"],
    )
    op.create_unique_constraint(
        "uq_skill_category_proposal",
        "skill_category_approvals",
        ["proposal_id"],
    )


def downgrade() -> None:
    op.drop_table("skill_category_approvals")