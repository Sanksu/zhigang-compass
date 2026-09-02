"""create skill_descriptions (技能解释覆盖表)

Revision ID: 20260902_001
Revises: 20260830_001
Create Date: 2026-09-02 15:00:00.000000

背景：岗位画像技能节点需要一个"对技能的解释"。技能在白名单/图谱中只有
name→category 映射，无语义说明。skill_descriptions 承载管理员手工编辑与
LLM 补齐写入的解释覆盖（可持久化），读取优先级：
SkillDescription（DB 覆盖）> 内置词典 SKILL_DESCRIPTIONS > 整合模板。

- skill_name：标准技能名（图 Skill.name，唯一）
- description：解释正文
- source：manual（人工编辑） / llm（LLM 补齐）
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260902_001"
down_revision = "20260830_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skill_descriptions",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("skill_name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="manual"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_skill_descriptions_skill_name", "skill_descriptions", ["skill_name"])
    op.create_unique_constraint(
        "uq_skill_description_name",
        "skill_descriptions",
        ["skill_name"],
    )


def downgrade() -> None:
    op.drop_table("skill_descriptions")