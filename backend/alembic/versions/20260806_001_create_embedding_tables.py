"""create pgvector embedding tables

Revision ID: 20260806_001
Revises: 20260805_004
Create Date: 2026-08-06 09:00:00.000000

设计文档 §11.4.3「pgvector 向量集合」三表落地：
- skill_embeddings（IVFFLAT, Cosine）：技能相似度检索（Sentence-BERT MiniLM 384 维）
- project_embeddings（IVFFLAT, Cosine）：项目与岗位场景匹配
- jd_embeddings（HNSW, Cosine）：JD 语义去重辅助

索引参数遵循设计文档 §11.4.3：IVFFLAT lists=100, probe=10；HNSW m=16, ef_construction=64。
表结构遵循 `id UUID, embedding vector(384), metadata JSONB`（skill_embeddings.id 取
Neo4j skill_id 字符串，便于按技能直接定位向量）。
"""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260806_001"
down_revision = "20260805_004"
branch_labels = None
depends_on = None

_VEC_DIM = 384


def _create_indexes() -> None:
    # IVFFLAT（技能/项目）：lists=100（设计文档 §11.4.3）
    op.execute(
        "CREATE INDEX ix_skill_embeddings_vector ON skill_embeddings "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )
    op.execute(
        "CREATE INDEX ix_project_embeddings_vector ON project_embeddings "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )
    # HNSW（JD 语义去重辅助）：m=16, ef_construction=64（设计文档 §11.4.3）
    op.execute(
        "CREATE INDEX ix_jd_embeddings_vector ON jd_embeddings "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "skill_embeddings",
        sa.Column("id", sa.String(64), primary_key=True),  # Neo4j skill_id
        sa.Column("embedding", Vector(_VEC_DIM), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "project_embeddings",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("embedding", Vector(_VEC_DIM), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        # 业务唯一键（resume_id + project_index）：保证重跑幂等 upsert
        sa.Column("resume_id", sa.String(64), nullable=False),
        sa.Column("project_index", sa.Integer, nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("resume_id", "project_index", name="uq_project_embeddings_resume_project"),
    )

    op.create_table(
        "jd_embeddings",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("embedding", Vector(_VEC_DIM), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("jd_id", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    _create_indexes()


def downgrade() -> None:
    op.drop_table("jd_embeddings")
    op.drop_table("project_embeddings")
    op.drop_table("skill_embeddings")
    # 不删除 vector 扩展：occupations.embedding 仍依赖（设计文档 11.4.3 向量集合）
