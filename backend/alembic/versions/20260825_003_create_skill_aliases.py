"""create skill_aliases (技能别名回写通道)

Revision ID: 20260825_003
Revises: 20260825_002
Create Date: 2026-08-26 09:00:00.000000

方案①：LLM 发现技能别名（缩写/中英/版本变体等"向量不相似但语义等价"类）
→ 人工审批 → 回写。skill_aliases 单表既是审批事实（variant→standard_name，
approve 后 status=approved），也是运行时归一化并查源（normalize_skill 只读
approved 行）。

语义：
- variant：LLM 发现的别名（如 JS、c语言、Python3）
- standard_name：归并目标（已知标准名，gate 守护防虚构）
- status：pending（LLM 建议待审）→ approved（人工批准，normalize 并查）
  → rejected
- unique(variant) 去重（同一别名单次生效）
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260825_003"
down_revision = "20260825_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("variant", sa.String(length=128), nullable=False),
        sa.Column("standard_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("proposal_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=24), nullable=False, server_default="llm_review"),
        sa.Column("reviewed_by", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("review_reason", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("applied_to_graph", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_skill_aliases_variant", "skill_aliases", ["variant"])
    op.create_index("ix_skill_aliases_status", "skill_aliases", ["status"])
    op.create_unique_constraint(
        "uq_skill_alias_variant",
        "skill_aliases",
        ["variant"],
    )


def downgrade() -> None:
    op.drop_table("skill_aliases")
