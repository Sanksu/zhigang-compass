# AGENTS.md — AI 协作入口

> 本文件是 AI 智能体（Cursor / Claude Code / Trae / Copilot 等）在本项目工作的**单一入口**。
> 任何 AI 智能体首次进入项目时，必须先完整阅读本文件，再阅读相关模块的子文档。
> 智能体在工作中应主动引用本文件的规则，违反铁律的输出视为无效产出。

---

## 1. 项目身份卡

| 字段 | 值 |
|------|---|
| 项目名 | 智岗罗盘——多源异构驱动的岗位能力动态演化与人岗匹配系统 |
| 项目编号 | XH-202621 |
| 赛事 | 科大讯飞挑战杯揭榜挂帅 |
| 项目周期 | 2026.07.13 — 2026.09.05 |
| 团队规模 | 6 人（前端/后端/算法/数据/测试/文档） |
| 仓库根 | `zhigang-compass/` |

## 2. 文档导航（按阅读优先级）

| 顺序 | 文档 | 性质 | 必读章节 |
|------|------|------|---------|
| 1 | [AGENTS.md](./AGENTS.md)（本文件） | AI 协作入口 | 全文 |
| 2 | [docs/README.md](./docs/README.md) | 文档索引 | 按角色定位阅读路径 |
| 3 | [docs/design/设计文档.md](./docs/design/设计文档.md) | **设计文档**（单一事实源） | 与任务相关章节 |
| 4 | [docs/design/执行计划.md](./docs/design/执行计划.md) | **执行计划** | 任务所属模块章节 |
| 5 | [docs/guides/贡献指南.md](./docs/guides/贡献指南.md) | 协作规范 | 分支策略 + Commit 规范 + PR 流程 |
| 6 | [docs/project/项目概览.md](./docs/project/项目概览.md) | 项目概览 | 技术栈 + 目录结构 |
| 7 | [docs/guides/团队启动指南.md](./docs/guides/团队启动指南.md) | 启动指南 | 环境配置（仅首次） |
| 8 | 各模块 README | 模块导航 | 进入具体模块前 |

**文档分层原则**：
- `docs/design/设计文档.md` = 设计文档（What & Why）：系统设计方案的单一事实源
- `docs/design/执行计划.md` = 执行计划（When & Who）：分阶段任务、关键路径、时间节点
- `AGENTS.md` = AI 协作入口（How for AI）：智能体工作规则
- `docs/design/Code-Wiki.md` = 代码导航：架构分层、模块职责、关键类/函数
- `docs/project/进度跟踪.md` = 进度审计：基于源码的阶段性快照

## 3. 三条不可松绑的铁律

### 铁律一：契约优先

- API 变更必须先改 `backend/openapi/openapi.yaml`（单一事实源），再写后端实现，最后前端基于契约生成类型
- 前端不得直接定义与后端不一致的类型，必须通过 `openapi-typescript` 从契约生成
- 智能体输出 API 代码时，必须同时输出契约片段
- 契约文件路径：`zhigang-compass/backend/openapi/openapi.yaml`

### 铁律二：main 可运行

- 不允许向 `main` 直推，必须经 PR + CI 全绿 + ≥ 1 人 Review
- 智能体输出代码前必须自检：是否破坏现有 import？是否引入未声明的依赖？是否破坏 `.env.example`？

## 4. 智能体工作规则

### 4.1 任务分类与红线

| 任务类型 | AI 自主产出 | 必须人工把关 |
|---------|-------------|-------------|
| 样板代码（CRUD / Schema / 类型） | ✅ 允许 | 仅 Review 格式 |
| 单元测试草稿 | ✅ 允许 | 断言逻辑须人工确认 |
| 文档与注释 | ✅ 允许 | 技术准确性须人工确认 |
| 正则/SQL/Cypher 草稿 | ✅ 允许 | 须人工验证执行结果 |
| **安全相关代码**（密钥/Token/认证/权限/文件上传） | ❌ 禁止自主 | 必须人工逐行审查 |
| **算法核心**（LLM 抽取/匹配引擎/演化算法/幻觉防控三道防线） | ⚠️ 仅参考 | 算法岗张恺天亲自把关 |
| **业务正确性**（JD 解析/简历抽取/SimHash 去重/置信度阈值） | ⚠️ 仅参考 | 须对照设计文档.md 验证 |
| 依赖版本选型 | ❌ 禁止自主 | 须与设计文档.md 2.2 节技术栈一致，若必须更改需人工审核 |

### 4.2 上下文加载清单

智能体首次进入项目时，应按以下顺序加载上下文：

1. 本文件（AGENTS.md）全文
2. `docs/project/项目概览.md` — 了解项目定位与技术栈
3. `docs/design/设计文档.md` 中与任务相关章节（不要全文加载，避免上下文爆炸）
4. `docs/design/执行计划.md` 中任务所属模块章节
5. 目标模块的 README（如 `backend/README.md`）

### 4.3 分支策略

智能体开发前必须按本节切分支，禁止在 `main` / `develop` 上直接提交。

**基线分支**：所有 feature/fix 分支从 `develop` 切出，hotfix 从 `main` 切出。

**分支命名公式**：`<类型>/<模块前缀>-<简述>`
- 类型：`feature` | `fix` | `hotfix` | `docs`
- 模块前缀：`fe` | `be` | `algo` | `data` | `test` | `docs`
- 简述：英文短横线分隔，≤ 30 字符

| 任务场景 | 切分支命令 | 合并目标 | 生命周期 |
|---------|-----------|---------|---------|
| 新功能开发 | `git checkout develop && git pull && git checkout -b feature/be-user-auth` | `develop`（PR） | ≤ 3 天 |
| Bug 修复 | `git checkout develop && git pull && git checkout -b fix/algo-match-score` | `develop`（PR） | ≤ 3 天 |
| 生产紧急修复 | `git checkout main && git pull && git checkout -b hotfix/neo4j-conn-leak` | `main` + `develop` | 立即 |
| 文档更新 | `git checkout develop && git pull && git checkout -b docs/api-spec` | `develop`（PR） | ≤ 3 天 |

**智能体操作铁律**：
- ❌ 禁止直推 `main` / `develop`，必须经 PR + ≥ 1 人 Review
- ❌ 禁止跨基线切分支（feature 必须从最新 `develop` 切出，先 `git pull`）
- ❌ 禁止分支生命周期 > 3 天，超出必须拆分或合并
- ✅ 单 PR 改动 ≤ 500 行，超出必须拆分
- ✅ 模型变更（Alembic 迁移）须在 PR 描述中标注，权限收归后端 + 算法负责人
- ✅ PR 标题沿用 Commit 规范：`<type>(<scope>): <description>`

**工作流优先级**（避免空等）：**测试 > 算法 > 后端 > 前端**

### 4.4 禁止事项

- ❌ 禁止读取 `.env`、密钥文件、用户数据
- ❌ 禁止向外部 AI 服务粘贴项目敏感信息（API Key / 用户数据 / 简历内容）
- ❌ 禁止引入设计文档.md 2.2 节技术栈之外的依赖
- ❌ 禁止生成防御性 fallback / 兼容层（除非方案明确要求）
- ❌ 禁止在产品代码中留调试日志、临时文件、TODO 占位符
- ❌ 禁止修改 `AGENTS.md` 本身（仅提醒与建议，不直接修改）

## 5. 模块导航

| 模块 | 路径 | 主责 | 关键约束 |
|------|------|------|---------|
| 前端 | `frontend/` | 黄唐尧 | React 19 + TS strict + ECharts（2D 主）+ react-force-graph-3d（3D 可选） |
| 后端 | `backend/` | 马兴达 + 张恺天 + 刘琪 | FastAPI + 算法引擎（app/services/）+ 数据管线（data/）+ 测试（tests/）+ openapi 契约 |
| 测试 | `backend/tests/` | 王鹏羽 | 测试金字塔 70/20/10 + 黄金集 JSONL 规范 |
| 文档 | `docs/` | 张怀伟 | 非技术岗，使用 GitHub Desktop 提交 |

## 6. 关键技术约束（不可违反）

### 6.1 数据栈

- **PostgreSQL 15 + pgvector 扩展**：关系数据 + 向量检索 + JSONB 快照存储（一份镜像承载三能力）
- **Neo4j 5**：图数据库 + 全文索引（cjk 分词器覆盖中文搜索，替代 Elasticsearch）+ 30s Redis 短 TTL 缓存（panorama 端点）
- **Redis 7**：缓存 + 限流 + 任务队列
- **禁止**引入 Elasticsearch / Milvus / MinIO / etcd / Nginx / Ollama 等额外服务

### 6.2 部署栈

- Docker Compose **5 服务**：`api / postgres(pgvector) / redis / neo4j / worker(ARQ)`
- FastAPI 通过 `StaticFiles` 同端口托管前端静态资源，中间件承担 CORS/CSP/HSTS/gzip/限流
- 一键 `docker compose up -d` 启动，所有服务通过健康检查
- 生产环境 fail-fast：`APP_ENV=production` 时强校验 SECRET_KEY 长度、Redis 密码、禁用 Swagger

### 6.3 图谱可视化

- **默认 2D**：`ECharts` 力导向图，≥ 100 节点 @ 60fps
- **可选 3D**：`react-force-graph-3d`（动态加载），WebGL2 不可用时自动降级至 2D
- 平板/移动端固定 2D 模式，桌面端支持用户切换

### 6.4 LLM 集成

- 必须使用 **OpenAI 兼容 API**，通过 `base_url` + `api_key` 切换 provider
- **多 provider 同步重试链**：provider 组合与优先级为运行时可配置项（不限定厂商），管理员可经管理后台前端页面（`/admin/llm`）修改并持久化（api_key 打码不回显），无需改代码
- 同步路由：主 API 超时 10s 即返回 504（错误码 5003，§2.4.7），不重试，避免同步阻塞
- 异步任务（ARQ）：按优先级依次尝试，30s × 3 = 90s 上限，全部失败后入延迟队列
- LLM 输出必须经 **Pydantic Schema 强校验**（幻觉防控第一道防线）
- 配置文件：`configs/llm_providers.yaml`（单一事实源）

### 6.5 数据采集

- 13 源 A/B/C 三级分级（拉勾网 2026-08-01 移除，原 14 源）
- 国内单 IP 直连 + 国际走代理池（ProxyPoolMiddleware 随机轮换，失败自动剔除）
- 黄金集 M2 末 ≥ 50 条 / M3 中段 ≥ 100 条（题目硬性要求）

## 7. 项目记忆规则

- 接手项目前先检查 `docs/postmortems/`（如有），了解历史踩坑
- 完成大型任务后，若有值得沉淀的规则，提醒用户记录至 `docs/postmortems/`
- 项目级规则变更须经用户确认后写入 `project_memory.md`，不直接修改 `AGENTS.md`

## 8. 常用命令速查

```bash
# 启动开发环境（5 服务：api/postgres/redis/neo4j/worker，FastAPI 托管前端）
docker compose up -d

# 后端开发
cd backend && uv run uvicorn app.main:app --reload --port 8000

# 前端开发
cd frontend && pnpm install && pnpm dev

# 数据库迁移
cd backend && uv run alembic upgrade head

# Neo4j 建库
cd backend && uv run python scripts/init_neo4j.py

# 准确率评测
cd backend && uv run python scripts/evaluate.py --task all

# 测试
cd backend && uv run pytest --cov=app
cd frontend && pnpm test
```

## 9. 联系与升级

- 协作问题：项目群 @对应模块负责人
- 文档错误：在 GitHub Discussions 提 issue
- 本文件修改建议：在 PR 中提出，由项目负责人 Review 后合并

---

> **最后提醒**：任何代码进入 `main` 前，必须经过人工 Review。安全/算法/业务逻辑三道红线永不可松绑。
