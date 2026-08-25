"""create name_normalization_requests (名称归一审批执行通道)

Revision ID: 20260825_001
Revises: 20260824_003
Create Date: 2026-08-25 09:00:00.000000

position_normalize / skill_normalize（PR3 c）proposal→approved 后的持久化目标：
审核同意的 rename/merge 先落本表（PG 单一事实源之一），由
scripts/sync_dynamic_normalization.py 幂等应用到 Neo4j（改名/并入）。
源=position_normalize / skill_normalize 决策记录 approval（LLM 提议 + 人工审批）。

语义：
- entity_type=skill，action=merge：source_name 技能节点并入 target_name
- entity_type=position，action=merge：source_name 岗位节点并入 target_name
- action=rename：source_name 节点改名为 target_name
- unique(proposal_id) 幂等去重（approve 预查拒绝重复批准）
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260825_001"
down_revision = "20260824_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "name_normalization_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("entity_type", sa.String(length=20), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("source_name", sa.String(length=128), nullable=False),
        sa.Column("target_name", sa.String(length=128), nullable=False),
        sa.Column("primary_node_name", sa.String(length=128), nullable=False),
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
        "ix_name_normalization_requests_entity",
        "name_normalization_requests",
        ["entity_type"],
    )
    op.create_index(
        "ix_name_normalization_requests_proposal",
        "name_normalization_requests",
        ["proposal_id"],
    )
    op.create_unique_constraint(
        "uq_name_normalization_proposal",
        "name_normalization_requests",
        ["proposal_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_name_normalization_proposal", "name_normalization_requests", type_="unique")
    op.drop_index("ix_name_normalization_requests_proposal", table_name="name_normalization_requests")
    op.drop_index("ix_name_normalization_requests_entity", table_name="name_normalization_requests")
    op.drop_table("name_normalization_requests")
