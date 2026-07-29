"""ARQ Worker 配置。"""

from app.core.config import settings

ARQ_SETTINGS = {
    "redis_settings": settings.arq_redis_url,
    "concurrency": settings.arq_concurrency,
    "task_timeout": settings.arq_task_timeout,
    "max_retries": 2,
    "retry_delay": 10,
}
