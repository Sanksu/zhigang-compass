"""应用配置，所有环境变量通过 pydantic-settings 读取。

优先级：环境变量 > .env 文件 > 默认值。
"""

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------- 应用 ----------
    app_name: str = "智岗罗盘"
    app_env: str = "development"  # development | production
    debug: bool = True
    secret_key: str = "change-me-in-production"

    # ---------- 数据库 ----------
    postgres_dsn: str = (
        "postgresql+asyncpg://zhigang:zhigang@localhost:5432/zhigang"
    )
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password"
    redis_url: str = "redis://localhost:6379/0"

    # ---------- JWT ----------
    jwt_private_key_path: str = "keys/private.pem"
    jwt_public_key_path: str = "keys/public.pem"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7

    # ---------- 初始管理员（首次启动用，生产环境务必修改） ----------
    admin_username: str = "admin"
    admin_password: str = "admin123"

    # ---------- 缓存 ----------
    panorama_cache_ttl: int = 30  # 秒

    # ---------- 前端 ----------
    frontend_dist_dir: str = "../frontend/dist"
    cors_origins: list[str] = ["*"]

    # ---------- ARQ ----------
    arq_redis_url: str = "redis://localhost:6379/1"
    arq_concurrency: int = 10
    arq_task_timeout: int = 1800  # 30 分钟；须大于爬虫 subprocess 上限 900s（BOSS 多任务翻页）

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def _backend_dir(self) -> Path:
        return Path(__file__).resolve().parent.parent.parent

    @property
    def jwt_private_key(self) -> str:
        p = Path(self.jwt_private_key_path)
        if not p.is_absolute():
            p = self._backend_dir / self.jwt_private_key_path
        return p.read_text()

    @property
    def jwt_public_key(self) -> str:
        p = Path(self.jwt_public_key_path)
        if not p.is_absolute():
            p = self._backend_dir / self.jwt_public_key_path
        return p.read_text()


settings = Settings()
