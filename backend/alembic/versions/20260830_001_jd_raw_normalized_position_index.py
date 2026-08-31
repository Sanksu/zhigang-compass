"""jd_raw snapshot->>'normalized_position' 表达式索引（第八轮 P1-11）

Revision ID: 20260830_001
Revises: 20260825_003
Create Date: 2026-08-30 12:00:00.000000

背景：portrait_evidence.py / propose_normalization.py 以
snapshot["normalized_position"].astext 谓词过滤 jd_raw（爬虫主表，持续增长），
无索引时顺序全表扫——岗位画像/归一提议端点随数据量线性劣化。
表达式索引 CREATE INDEX ON jd_raw ((snapshot->>'normalized_position')) 覆盖该谓词。

注意：与 SQLAlchemy 模型无对应列（表达式索引不进 models/raw.py），
此迁移为纯性能优化，downgrade 无数据风险。
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260830_001"
down_revision = "20260825_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_jd_raw_snapshot_normalized_position",
        "jd_raw",
        [sa.text("(snapshot->>'normalized_position')")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_jd_raw_snapshot_normalized_position",
        table_name="jd_raw",
    )
