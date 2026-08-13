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
COPY pyproject.toml uv.lock .
# 镜像瘦身（2026-08-09）：uv sync 装完依赖后清理字节码缓存与临时文件
# （site-packages 体积由 torch/paddle 等重型依赖决定，见下方说明）。
# --frozen 按仓库 uv.lock 精确安装，不在构建时重新 lock（保证可复现）
RUN uv sync --no-dev --frozen
# 清理构建产物：pip/uv 缓存、Python 字节码缓存（site-packages 瘦身，可省数百 MB）
RUN find /usr/local/lib/python3.11/site-packages -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
RUN find /usr/local/lib/python3.11/site-packages -name "*.pyc" -delete 2>/dev/null || true
RUN rm -rf /root/.cache /tmp/pip-* /tmp/uv-*

# 注：torch 瘦身说明 —— 本项目 SBERT（技能相似度）/简历语义/OCR 均 CPU 推理，
# 但 torch 是 sentence-transformers 的传递依赖，uv.lock 锁定 PyPI manylinux CUDA
# wheel（~526MB，解压 ~2.5GB），Docker 镜像体积主要由此造成。
# 若需进一步瘦身（省 ~2GB）：在 pyproject.toml 声明
#   [[tool.uv.index]] name="pytorch-cpu" url="https://download.pytorch.org/whl/cpu"
#   [tool.uv.sources] torch = { index = "pytorch-cpu" }
# 并重新 uv lock（见 2026-08-09 尝试：直接 Dockerfile pip 预装 CPU torch 会与
# uv sync --frozen 冲突，必须走 pyproject sources 声明式方案）。

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