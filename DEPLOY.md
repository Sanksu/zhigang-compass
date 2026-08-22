# 智岗罗盘部署说明（DEPLOY.md）

> 状态：**定稿**（2026-08-15）——基于 08-13 首次容器部署演练实测（api/worker 镜像首次构建 + 5 服务全链路 12 项冒烟全通）+ 08-15 性能压测验证（TE-M5-01 前置，panorama/search P95 < 500ms @ 100 并发，见 docs/perf_baseline_20260815.md）
> 对应任务：执行计划 2.2 后端 M5「部署文档完善」+ 2.6 文档 M5「DEPLOY.md 部署说明」（DO-M5-03）

---

## 1. 前置要求

| 项 | 要求 | 说明 |
|----|------|------|
| Docker | 24+ / Compose v2 | `docker compose version` 验证 |
| 磁盘 | **≥ 30G 空闲** | api 镜像 site-packages 约 6.65GB（torch/paddle 主导），构建缓存另计 |
| 网络 | Docker Hub 可达 | 基础镜像直连可拉；偶发 auth token 抖动重试即可 |

## 2. 配置准备

```bash
# 1. 复制模板并填写
cp backend/.env.example backend/.env

# 2. 预建 dict-guard 动态过滤空层（compose 单文件挂载要求宿主文件先存在；
#    缺失时 Docker 会创建同名目录，写入永久失败须重建容器）
printf '{\n  "version": 0,\n  "blocked": [],\n  "protected": []\n}\n' > backend/configs/skill_filters_dynamic.json
```

> 注意：不要 `cp` `skill_filters_dynamic.json.example`——其中示例条目（示例噪音词/示例保护词）会被当作真实动态层生效。运行后该文件由 dict-guard 每日评估与管理端审批自动维护，api/worker ≤30s 热同步。

> ⚠️ **JWT 签名密钥**（`backend/keys/private.pem` + `public.pem`，git 不入库）经 compose 挂载 `./backend/keys:/app/keys` 注入，**不入镜像**。api 启动时 fail-fast 校验（2026-08-22 登录「服务器内部错误」事故：容器按旧 compose 创建、缺该挂载，refresh 全线 500）。密钥缺失时 api 拒绝启动并提示 `docker compose up -d --force-recreate api worker`。

> ⚠️ **compose 挂载/环境变更后必须 `docker compose up -d --force-recreate`**：`up -d` 对已存在且配置未变 hash 的容器不会重建；若曾在其他分支/旧配置下 up 过，配置回不来也不会自动补挂载（keys/reports/configs 等注入全部失效且无提示）。跨分支操作后统一 `--force-recreate` 一次最稳。

**必改项**（production 下 fail-fast 强校验，不满足则 api 拒绝启动）：

| 键 | 要求 | 缺省后果 |
|----|------|---------|
| `SECRET_KEY` | 非 `change-me-in-production` | 启动报错退出 |
| `ADMIN_PASSWORD` | 非 `admin123`，**必须存在** | 启动报错退出（弱口令门禁） |
| `POSTGRES_DSN` / `NEO4J_URI` / `REDIS_URL` | 指向实际服务 | 容器内由 compose environment 覆盖，本地开发才需改 |

其余可选项（LLM provider、CDP、代理等）见 `.env.example` 注释。

**前端产物**（api 容器以只读卷挂载托管）：

```bash
cd frontend && pnpm install && pnpm build   # 生成 frontend/dist
```

## 3. 一键启动

```bash
docker compose up -d          # 5 服务：api / postgres / redis / neo4j / worker
docker compose ps             # 全部 healthy 即就绪
```

- api 容器 ENTRYPOINT **自动执行 `alembic upgrade head`**（无需手动迁移）
- 首次启动约 40–60s（镜像构建另计；healthcheck start-period 10s）

### 3.1 日常更新部署（⚠️ 合并 ≠ 部署）

develop 合入 PR 后，**运行中的栈不会自动更新**——api/worker 的代码在镜像里（worker 无任何代码挂载，镜像即代码），前端在 `frontend/dist` 只读挂载。08-22 曾因「#404 修复已合 develop 但镜像未重建」导致三课程源爬虫全灭，同日软技能合入后也漏跑过存量回填。每次合并后按此清单部署：

```bash
git checkout develop && git pull

# ① 后端有改动（app/ alembic/ configs/ pyproject）→ 重建镜像，否则 worker 仍跑旧代码
docker compose -f docker-compose.yml build api worker

# ② 前端有改动 → 重建 dist（bind mount 只读挂载，up 即生效，无需重建容器）
cd frontend && pnpm install && pnpm build && cd ..

# ③ compose 挂载的宿主文件必须先存在（Docker 对缺失的挂载源会建同名目录，写入永久失败）
ls -la backend/configs/skill_filters_dynamic.json   # 应为文件而非目录

# ④ 起栈（显式 -f 排除本地开发 override——override 会把 backend/app 挂载进 api，
#    掩盖镜像与工作区的代码差异，08-22 部署教训）
docker compose -f docker-compose.yml up -d
docker compose ps                                    # 全部 healthy
curl http://localhost:8000/health
```

- **回填脚本检查**：CHANGELOG 对应条目若标注存量回填（如 `backfill_skill_category.py`），部署后在容器内执行一次（幂等）：`docker exec zhigang-api python scripts/backfill_xxx.py`
- **迁移**：api ENTRYPOINT 自动执行，无需手动；但镜像必须包含新迁移文件（见 ①，迁移缺失=部署后启动失败）
- 可选加速：`CD (images)` workflow 在 develop 的**后端相关改动**合入后把镜像推到 GHCR（`ghcr.io/sanksu/zhigang-compass-api|worker`，公开仓库免认证拉取；纯前端/文档合并不构建 12.7GB 后端镜像），可 `docker pull` + `docker tag` 为本地镜像名（`zhigang-compass-api:latest` / `zhigang-compass-worker:latest`）替代 ① 的本地构建

## 4. 初始化

- **数据库迁移**：自动（见上）
- **管理员账号**：users 表为空时，首次登录用 `ADMIN_PASSWORD` 自动 bootstrap 创建（仅非 production 路径）；**production 下 bootstrap 禁用**，若库中已有 admin 用户需手动重置密码哈希（见 §7.2）

## 5. 验证（冒烟清单）

| # | 验证项 | 命令 |
|---|--------|------|
| 1 | 健康端点 | `curl http://localhost:8000/health` → `{"status":"healthy"}` |
| 2 | 前端静态托管 | `curl http://localhost:8000/` → 智岗罗盘 index.html |
| 3 | 图谱 panorama | `curl http://localhost:8000/api/v1/graph/panorama`（匿名，Redis 30s 缓存） |
| 4 | 认证链路 | `POST /api/v1/auth/login`（admin）→ `/api/v1/auth/me` |
| 5 | 全文检索 | `GET /api/v1/graph/search?q=Python`（cjk 全文索引） |
| 6 | worker | `docker logs zhigang-worker` → ARQ 启动 + cron 实跑 |

首次完整演练（2026-08-13）12 项冒烟全通过，详见进度跟踪 §6.0.3。

**性能验证（2026-08-15）**：Locust 100 并发 3 分钟——panorama P95 430ms / search P95 390ms（目标 <2s，达标），详见 `docs/perf_baseline_20260815.md`。

## 6. 运维

| 操作 | 命令 |
|------|------|
| 停止（保留数据） | `docker compose down`（数据卷不删；`-v` 才删卷） |
| 查看日志 | `docker compose logs -f api` / `-f worker` |
| 重启单服务 | `docker compose restart api` |
| 更新代码 | 见 **§3.1 合并 ≠ 部署清单**（pull → build → 前置检查 → up） |
| 数据备份 | `pg_dump`（PostgreSQL）+ `neo4j-admin dump`（Neo4j）；数据卷：`pg_data` / `neo4j_data` / `neo4j_logs` / `redis_data` |

### 6.1 ETL 调度（容器内 ARQ cron，08-21 #348）

ETL 主管线（采集 → 去重 → LLM 抽取 → 时滞/通胀 → 入图 → 快照 → 发现/自动流转）由 **worker 容器内 ARQ cron** 触发，不再依赖外部计划任务：

- 执行时间在**配置中心 →「ETL 队列」页**配置（`etl_run_hour` / `etl_run_minute`，默认 05:00），持久化到 `backend/configs/runtime_settings.json`
- 修改后需 **重启 worker** 生效：`docker compose restart worker`
- 当日幂等：`run_etl_pipeline_scheduled` 内部 Redis 锁（`arq:etl:run:{date}`，24h TTL），重复触发自动跳过
- 已验证：`docker logs zhigang-worker` 可见 ARQ cron 注册与 ETL 入队/执行日志

> 历史外部任务（Windows `scheduled_tasks.ps1` 的 `ETLDaily` / Linux `crontab.example` 的 `0 5 * * *`）已停用；`scripts/cron/etl_daily.py` 保留，供手动重跑（`--force`）。其余外部任务（maimai/linkedin/课程源、GraphHealth、PositionDup）不变。

## 7. 常见问题

### 7.1 api 容器 Exited（fail-fast 门禁）
`docker logs zhigang-api` 提示 `SECRET_KEY 未修改` / `ADMIN_PASSWORD 仍为默认弱口令` → 修正 `backend/.env` 后 `docker compose up -d api`。

### 7.2 已有 admin 用户时登录 4010
production 下 bootstrap 路径禁用（auth.py 直接 4010），且 `.env` 的 `ADMIN_PASSWORD` 只作用于 bootstrap——需在容器内重置哈希（与 `.env` 保持一致）：

```bash
docker exec zhigang-api python -c "
import asyncio
from sqlalchemy import update
from app.core.security import hash_password
from app.core.database import engine
from app.models.business import User

async def main():
    async with engine.begin() as conn:
        await conn.execute(update(User).where(User.username == 'admin').values(password_hash=hash_password('你的密码')))
asyncio.run(main())
"
```

### 7.3 简历解析后 compare 返回 4040「简历不存在」
`POST /api/v1/resume/parse` 为异步任务（返回 `task_id = resume_id`），LLM 抽取完成前调用 compare 会 4040——等待任务完成（约 10–30s）后再调用。

### 7.4 磁盘不足
`docker system prune` 清理悬空镜像/缓存；如需大幅瘦身：torch 换 CPU wheel（见 backend/Dockerfile 顶部说明，需重新 uv lock）。

### 7.5 Neo4j 未启动时接地降级
grounding 双路检索中 Neo4j 不可达自动降级 ILIKE（设计内，不阻塞）；启动 neo4j 后重跑 `scripts/import_occupations.py --source hrss --csv-dir data/hrss`（幂等）即可同步 Occupation 节点。

### 7.6 Docker Hub 偶发 token 失败
`failed to fetch anonymous token` 为瞬时网络抖动，重试 `docker compose up -d --build` 即可；基础镜像拉取直连可用。

---

> 补充：环境变量完整清单与本地开发配置见 `docs/guides/团队启动指南.md`；架构与部署设计见 `docs/design/设计文档.md` §11。
