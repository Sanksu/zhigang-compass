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

# 非 root 用户
RUN useradd -m -u 1000 app && chown -R app:app /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

ENTRYPOINT ["sh", "-c", "uv run alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]