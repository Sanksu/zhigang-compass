"""数据库连接管理：PostgreSQL (async) + Neo4j + Redis。"""

from neo4j import AsyncGraphDatabase, GraphDatabase
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from redis.asyncio import Redis

from app.core.config import settings

# ---------- PostgreSQL (async) ----------
# 连接池参数（08-14 审查加固）：pool_pre_ping 防依赖抖动时复用失效连接，
# pool_size 默认 5 按 API 并发放大（100 并发 P95<2s 目标下的合理起步值）
engine = create_async_engine(
    settings.postgres_dsn,
    echo=settings.debug,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=1800,
)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with async_session_factory() as session:
        yield session


# ---------- Neo4j ----------
neo4j_driver = GraphDatabase.driver(
    settings.neo4j_uri,
    auth=(settings.neo4j_user, settings.neo4j_password),
    connection_timeout=10,   # 08-14 审查加固：依赖抖动时快速失败而非无限等待
    max_connection_lifetime=1800,
    # 压测扩容（08-15 TE-M5-01）：30→100 对齐 100 并发目标——30 连接在
    # panorama 缓存 miss/search 并发下排队严重（P95 14s 长尾根因之一）
    max_connection_pool_size=100,
)

# ---------- Neo4j（async，graph API 热路径专用） ----------
# P2 迁移（08-17）：graph API 热路径（panorama/search/view 等）改走 async
# 驱动直查，不再用 asyncio.to_thread 包同步 IO（100 并发下线程池饱和是
# P95 长尾根因之一）。连接池/auth/超时/生命周期参数与上方 sync 驱动完全
# 一致（pool 100 对齐 100 并发目标）。workers/算法/scripts 仍用 sync
# neo4j_driver；async_neo4j_driver 仅在 graph API 异步链路使用。
async_neo4j_driver = AsyncGraphDatabase.driver(
    settings.neo4j_uri,
    auth=(settings.neo4j_user, settings.neo4j_password),
    connection_timeout=10,
    max_connection_lifetime=1800,
    max_connection_pool_size=100,
)




# ---------- Redis (async) ----------
redis_client = Redis.from_url(settings.redis_url, decode_responses=True)


async def get_redis() -> Redis:
    return redis_client
