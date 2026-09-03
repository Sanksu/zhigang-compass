"""discovery_candidates 结构化岗位定义列

Revision ID: 20260903_002
Revises: 20260903_001
Create Date: 2026-09-03 23:30:00.000000

背景：赛题要求新岗位定义包含「岗位名称/核心职责/必备技能/加分技能/典型行业
应用场景」五字段。原 discovery 子系统仅落一列 definition_draft（自由文本）。
新增 definition_structured（JSONB）承载 RAG 阶段二 LLM 结构化生成的
core_duties/typical_scenarios；技能两项展示时从图谱 REQUIRES 证据边组装，
不落本列。存量行缺省 '{}'（或 null），端点按空对象兜底组装。
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "20260903_002"
down_revision = "20260903_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "discovery_candidates",
        sa.Column("definition_structured", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("discovery_candidates", "definition_structured")
