"""add occupation embedding column

Revision ID: 20260804_001
Revises: 20260802_003
Create Date: 2026-08-04 10:00:00.000000

T-06 RAG 接地双路：occupations 表新增 embedding 向量列（pgvector，384 维），
承载 Sentence-BERT 语义检索（设计文档 7.2.3「pgvector 向量检索（语义 top-10）」）。
列可空：脚本未生成向量（模型不可用）时语义路降级为关键词检索，不阻塞接地。
"""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision = "20260804_001"
down_revision = "20260802_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pgvector 扩展（docker-compose 使用 pgvector/pgvector:pg15 镜像，已内置）
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column(
        "occupations",
        sa.Column("embedding", Vector(384), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("occupations", "embedding")
    # 不删除扩展：其他表可能依赖 vector 类型（设计文档 11.4.3 向量集合）
