# script — 本地开发与运维脚本

存放项目的本地开发一键启动与运维辅助脚本。

## dev.ps1 — 本地开发一键启动

以独立进程启动 `api`（uvicorn :8000）与 `worker`（ARQ），可选启动前端 dev server（vite :5173），并幂等确保 docker 基础设施（postgres/redis/neo4j）可用。

### 用法

```powershell
.\script\dev.ps1                  # 确保基础设施 + 启动 api + worker
.\script\dev.ps1 -Frontend        # 额外启动前端 dev server
.\script\dev.ps1 -Restart         # 8000/api/worker 已运行时强制重启（加载最新代码）
.\script\dev.ps1 -SkipInfra       # 跳过 docker 基础设施检查
```

### 参数

| 参数 | 说明 |
| --- | --- |
| `-Frontend` | 额外启动前端 dev server（5173 已被占用时跳过） |
| `-Restart` | 8000/api/worker 已运行时先停止再启动（加载最新代码） |
| `-SkipInfra` | 跳过 docker 基础设施（postgres/redis/neo4j）检查 |

### 前置条件

- Docker Desktop 已启动（容器幂等 `docker compose up -d`）
- backend 依赖已装（`uv run` 可用）；frontend 依赖已装（`pnpm` 可用）

### 行为说明

- api 与 worker 是独立进程：api 用 `uv run python -m uvicorn`，worker 用 `uv run python -m arq app.workers.tasks.WorkerSettings`
- PYTHONPATH 需含 `backend;backend\data`（scrapy 爬虫模块在 data/crawlers）
- 日志写入 `logs/`（已 gitignore）：`api.log` / `worker.log` / `frontend.log`
- 幂等：8000 被占用且未加 `-Restart` 时不重复启动；worker 已在跑时不重复启动
- 强制 UTF-8 输出（`PYTHONIOENCODING=utf-8` / `PYTHONUTF8=1`），避免中文 Windows 上日志 GBK 乱码

### 注意事项

- worker 启动即消费 Redis 队列中遗留的 `crawl_platform` 任务，若队列有历史任务会被立即执行
- 国际源爬虫需 `HTTPS_PROXY=http://127.0.0.1:7890`（脚本不设置，需手动指定）
