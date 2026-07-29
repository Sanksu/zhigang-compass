"""数据库连接管理：PostgreSQL (async) + Neo4j + Redis。"""

from neo4j import GraphDatabase
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from redis.asyncio import Redis

from app.core.config import settings

# ---------- PostgreSQL (async) ----------
engine = create_async_engine(settings.postgres_dsn, echo=settings.debug)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with async_session_factory() as session:
        yield session


# ---------- Neo4j ----------
neo4j_driver = GraphDatabase.driver(
    settings.neo4j_uri,
    auth=(settings.neo4j_user, settings.neo4j_password),
)


def get_neo4j():
    with neo4j_driver.session() as session:
        yield session


# ---------- Redis (async) ----------
redis_client = Redis.from_url(settings.redis_url, decode_responses=True)


async def get_redis() -> Redis:
    return redis_client
