# ──────────────────────────────────────────────────────────────
# 智岗罗盘 API — 多阶段构建 Dockerfile
# ──────────────────────────────────────────────────────────────

# ---------- Stage 1: 依赖安装 ----------
FROM python:3.11-slim AS builder

ENV UV_SYSTEM_PYTHON=1
# 项目依赖直接装入 /usr/local（系统 site-packages + bin）：
# uv sync 默认建 /build/.venv，运行时 COPY 系统路径将缺失依赖
ENV UV_PROJECT_ENVIRONMENT=/usr/local

RUN pip install --no-cache-dir uv

WORKDIR /build
COPY pyproject.toml .
RUN uv lock && uv sync --no-dev --frozen

# ---------- Stage 2: 运行 ----------
FROM python:3.11-slim

# 运行时依赖（仅 system 包）
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 从 builder 拷贝 site-packages
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

WORKDIR /app

# 项目代码
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini .
COPY keys/ ./keys/
COPY configs/ ./configs/
# 爬虫项目（crawl_platform 以 data/crawlers 为 cwd 调 scrapy crawl）
COPY data/crawlers/ ./data/crawlers/

# 非 root 用户
RUN useradd -m -u 1000 app && chown -R app:app /app
# 预创建 CDP 浏览器 profile 根目录并归 app：否则 compose 以 root 创建
# boss-cookies.json 挂载的父目录，maimai 等需写 profile 的源会 PermissionError
RUN mkdir -p /home/app/.zhigang-compass && chown app:app /home/app/.zhigang-compass

# Playwright chromium（爬虫国际源 JS 渲染）。
# root 阶段安装二进制到公共路径，再 chown 给 app，避免 USER app 后读权限问题
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN python -m playwright install chromium --with-deps && chown -R app:app /ms-playwright
# 软链 playwright chromium 到系统路径：setup_boss_chrome.find_chrome() 的
# Linux 分支查找 /usr/bin/chromium，CDP 源（maimai 等）经它启动浏览器
RUN ln -sf $(ls -d /ms-playwright/chromium-*/chrome-linux64/chrome | head -1) /usr/bin/chromium

# crawlers 包位于 data/ 下，app 包位于项目根：
# scrapy 以 data/crawlers 为 cwd 启动时，import crawlers.* 需要 data/、
# PostgresPipeline 的 import app.* 需要项目根在 sys.path 上
ENV PYTHONPATH=/app:/app/data

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

ENTRYPOINT ["sh", "-c", "uv run alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]