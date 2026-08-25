"""create llm_decision_records (六域统一 LLM 决策信封)

Revision ID: 20260824_001
Revises: 20260823_001
Create Date: 2026-08-24 18:00:00.000000

主仓灰度底座：JD 抽取/名称归一/分类/簇命名/治理/技能关系的每次 LLM
结构化决策落一张表，统一携带输入哈希、证据引用、provider/model、
prompt/schema 版本、结构化输出、风险路由、审核与生效状态、回滚引用。

设计约束见 app.models.business.LLMDecisionRecord docstring；
risk_tier 由 app.services.llm_decision.risk_tier_for 统一裁决。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260824_001"
down_revision = "20260823_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_decision_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("domain", sa.String(length=30), nullable=False),
        sa.Column("entity_type", sa.String(length=30), nullable=False, server_default=""),
        sa.Column("entity_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("run_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("env", sa.String(length=16), nullable=False, server_default="production"),
        sa.Column("input_hash", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("evidence_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("provider", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("model", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("prompt_version", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("schema_version", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("structured_output", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("postprocessed_output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("gate_result", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("risk_tier", sa.String(length=8), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="shadow"),
        sa.Column("reviewer", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("review_reason", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("effects_applied", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("rollback_ref", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("fallback_reason", sa.String(length=200), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_llm_decision_records_domain", "llm_decision_records", ["domain"])
    op.create_index("ix_llm_decision_records_status", "llm_decision_records", ["status"])
    op.create_index("ix_llm_decision_records_run_id", "llm_decision_records", ["run_id"])
    op.create_index(
        "ix_llm_decision_domain_status", "llm_decision_records", ["domain", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_llm_decision_domain_status", table_name="llm_decision_records")
    op.drop_index("ix_llm_decision_records_run_id", table_name="llm_decision_records")
    op.drop_index("ix_llm_decision_records_status", table_name="llm_decision_records")
    op.drop_index("ix_llm_decision_records_domain", table_name="llm_decision_records")
    op.drop_table("llm_decision_records")