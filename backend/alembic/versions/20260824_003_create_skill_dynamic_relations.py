"""create skill_dynamic_relations (LLM 关系审批执行通道)

Revision ID: 20260824_003
Revises: 20260824_002
Create Date: 2026-08-24 19:30:00.000000

skill_relation（PR6）proposal→approved 后的持久化目标：审核同意的关系先落
本表（PG 单一事实源之一），由 scripts/sync_dynamic_relations.py 与 YAML 种子
共同幂等 MERGE 入图。源=skill_relation 决策记录 approval（LLM 提议 + 人工
审批），与 configs YAML 静态种子并列，图重同步不丢。
- ALTERNATIVE_OF 以对称语义存储（一条记录，同步时双向 MERGE）
- unique(source, target, relation_type) 幂等去重
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260824_003"
down_revision = "20260824_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_dynamic_relations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_skill", sa.String(length=128), nullable=False),
        sa.Column("target_skill", sa.String(length=128), nullable=False),
        sa.Column(
            "relation_type", sa.String(length=24), nullable=False,
            comment="PREREQUISITE_OF | BELONGS_TO | ALTERNATIVE_OF",
        ),
        sa.Column("direction", sa.String(length=12), nullable=False, server_default="a_to_b"),
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
        "ix_skill_dynamic_relations_src_tgt",
        "skill_dynamic_relations",
        ["source_skill", "target_skill"],
    )
    op.create_index(
        "ix_skill_dynamic_relations_type",
        "skill_dynamic_relations",
        ["relation_type"],
    )
    op.create_unique_constraint(
        "uq_skill_dynamic_relations_src_tgt_type",
        "skill_dynamic_relations",
        ["source_skill", "target_skill", "relation_type"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_skill_dynamic_relations_src_tgt_type", "skill_dynamic_relations", type_="unique")
    op.drop_index("ix_skill_dynamic_relations_type", table_name="skill_dynamic_relations")
    op.drop_index("ix_skill_dynamic_relations_src_tgt", table_name="skill_dynamic_relations")
    op.drop_table("skill_dynamic_relations")