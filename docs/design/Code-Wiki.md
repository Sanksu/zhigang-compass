# 智岗罗盘 · Code Wiki

> 多源异构驱动的岗位能力动态演化与人岗匹配系统
> 项目编号：XH-202621 · 科大讯飞挑战杯揭榜挂帅
> 本文档是代码层面的结构化导航，与 [设计文档.md](./设计文档.md)（What & Why）、[执行计划.md](./执行计划.md)（When & Who）互补。
> AI 智能体请先阅读 [../../AGENTS.md](../../AGENTS.md)。

---

## 0. 文档定位

| 文档 | 性质 | 关注点 |
|------|------|--------|
| `设计文档.md` | 设计文档（单一事实源） | 系统设计方案 What & Why |
| `执行计划.md` | 执行计划 | 分工 + 时间节点 + 关键路径 |
| `AGENTS.md` | AI 协作入口 | 智能体工作规则、铁律 |
| **`Code-Wiki.md`（本文件）** | 代码导航 | 架构分层、模块职责、关键类/函数、依赖关系、运行方式 |

> 当前项目处于 **M4 收官 + M4→M5 过渡**（2026.08.12 审计）。API 契约 58 paths 全部实现（无 501 占位），算法核心（抽取/匹配/演化/发现/图算法）均已落地并通过真实库验证。M5 打磨阶段（08.26-09.04）未开始：三项准确率 ≥90%、性能压测、部署与演示物料。

---

## 1. 项目概览

一套**可追溯、可验证、可动态更新**的「人才能力大脑」：以证据节点为核心，通过多源异构数据驱动岗位能力图谱构建、动态演化与人岗精准匹配。

### 核心能力

| 能力 | 量化目标 |
|------|---------|
| 简历解析准确率 | ≥ 90% |
| 人岗匹配准确率 | ≥ 90% |
| 新岗位定义采纳率 | ≥ 70% |
| 图谱节点规模 | 2D 模式 ≥ 100 节点 @ 60fps（3D 可选） |
| 证据引用覆盖率 | 100% |
| 推理延迟 P95 | ≤ 2s |
| 单日采集量 | P0 ≥ 100 条/日（国内 ≥ 60 + 国际 ≥ 40），P1 ≥ 200 条/日 |

### 项目里程碑

| 阶段 | 时间 | 状态 | 核心交付 |
|--------|--------|------|---------|
| M1 方案 | 2026.07.13—07.26 | ✅ 已完成 | 技术方案 + 协作规范 + API 契约 |
| M2 基座 | 2026.07.27—08.05 | ✅ 已完成 | 工程脚手架、Neo4j 建库、爬虫开发、清洗管线 |
| M3 核心 | 2026.08.06—08.15 | ✅ 已完成 | LLM 抽取、匹配引擎、图谱首版、JWT 封装 |
| M4 闭环 | 2026.08.16—08.25 | ✅ 已完成 | 简历匹配、管理后台、新岗位发现、学习路径 |
| M5 打磨 | 2026.08.26—09.04 | ⏳ 未开始（当前） | 准确率、性能压测、Docker 部署、PPT + 视频 |
| ★ 初审提交 | 2026.09.05 | ⏳ 未开始 | 文档 + 源码 + 部署说明 |

### 团队分工

| 模块 | 负责人 | 核心定位 |
|------|--------|----------|
| 前端 | 黄唐尧 | 图谱可视化与用户交互 |
| 后端 | 马兴达 | API 服务、数据库与部署 |
| 算法 | 张恺天 | 知识图谱、LLM 抽取、匹配与演化 |
| 数据 | 刘琪 | 多源采集、清洗与结构化 |
| 测试 | 王鹏羽 | 测试用例、准确率与性能评测 |
| 文档 | 张怀伟 | 方案文档、PPT 与演示视频 |

---

## 2. 整体架构

### 2.1 分层架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│  前端 SPA（frontend/）                                          │
│  Vite 6 + React 19 + TS strict + Tailwind v4 + shadcn 风格      │
│  Zustand（状态）+ TanStack Query（服务端缓存）+ ECharts（图谱） │
│  路由：公开 / 受保护 / 管理员三类，AppShell 布局，12 页面        │
└───────────────────────────┬─────────────────────────────────────┘
                            │ /api/v1/*  （Vite 代理 / StaticFiles 同端口）
┌───────────────────────────▼─────────────────────────────────────┐
│  API 层（backend/app/api/v1/）                                  │
│  FastAPI + Pydantic + 统一 APIResponse 契约                     │
│  auth / graph / match / resume / evolution / admin 六子路由     │
│  （openapi.yaml 58 paths，M1-M4 全量交付，无占位）              │
└───────────────────────────┬─────────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────────┐
│  服务层（backend/app/services/）                                │
│  extraction（JD/简历抽取）  kg（图谱写入/聚合）                  │
│  matching（匹配引擎）      evolution（技能演化）                │
│  discovery（新岗位发现）   graph_algorithms（PageRank/Louvain/  │
│                           最短路径/技能簇）                     │
│  diagnosis（LLM 诊断报告） rag（RAG 检索）                      │
│  embeddings（pgvector 存取） learning_path（学习路径）          │
│  data_quality（时滞/通胀/SimHash/交叉验证/多样性） alerting     │
└─────────┬─────────────────┬─────────────────┬───────────────────┘
          │                 │                 │
┌─────────▼───────┐ ┌───────▼───────┐ ┌───────▼───────────────────┐
│ PostgreSQL 15   │ │  Neo4j 5      │ │  Redis 7                  │
│ + pgvector      │ │  能力图谱     │ │  缓存(30s) + 限流 + ARQ   │
│ 关系/向量/JSONB │ │  全文索引(cjk)│ │  任务队列（db=1 独立）    │
└─────────────────┘ └───────────────┘ └───────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  数据管线（backend/data/crawlers/）                             │
│  Scrapy + Playwright，13 源 A/B/C 分级（monster 停采，保留代码）│
│  → CleaningPipeline（脱敏 + 指纹 + SimHash + 质量评分）→        │
│  PostgresPipeline（upsert）→ ARQ ETL 任务                       │
└───────────────────────────┬─────────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────────┐
│  异步任务（backend/app/workers/）ARQ                            │
│  20+ 任务：ETL 管线（run_etl_pipeline 11 阶段）/ 抽取 / 匹配 /  │
│  简历 / 演化快照 / 发现（discovery_daily 每日 05:30）           │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  LLM 多 Provider 重试链（OpenAI 兼容 API）                      │
│  优先级与组合运行时可配置（configs/llm_providers.yaml）         │
│  同步路由 10s 超时返 504（错误码 5003）；异步任务 90s 上限       │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 数据流

```
招聘平台（13 源，monster 停采）
   │ Scrapy + Playwright 采集
   ▼
JobItem（统一 schema）
   │ CleaningPipeline：SHA256 指纹去重 + 脉脉脱敏 + 文本标准化
   │ + SimHash 语义去重 + 质量评分 + 时效加权
   ▼
PostgreSQL jd_raw（upsert，source+source_id 去重）
   │ LLM 抽取管线（三道幻觉防线）
   ▼
Skill / Tool / Education / Certification 实体 + REQUIRES 关系
   │ 入图谱
   ▼
Neo4j 能力图谱（Position / Skill / Evidence / Course ...）
   │ 每日 05:00 聚合预计算 + 演化检测 + 自动快照
   ▼
匹配引擎（内容加权 + Sentence-BERT + 领域维度 + 规则/LLM 兜底）
   │
   ▼
人岗匹配结果 / 演化信号 / 新岗位候选 / 学习路径 / 诊断报告
```

### 2.3 关键设计原则

1. **契约优先**：API 变更先改 [openapi/openapi.yaml](../../backend/openapi/openapi.yaml)（58 paths，单一事实源），前端用 `openapi-typescript` 生成类型
2. **算法与后端解耦**：算法与后端通过共享数据层解耦读写，不通过 API 互调
3. **两层更新机制**：新证据实时入库（PostgreSQL + Evidence 节点），聚合预计算每日 05:00
4. **三道幻觉防线**：JSON Schema 强校验 → 别名归一化 → 白名单后过滤
5. **小基数保护**：频次 < 10 走 PROTECTED 态；冷启动走 Wilson score 兜底
6. **权重外部化**：所有权重默认值硬编码，可通过 `configs/*.json` 覆盖，文件缺失不阻断流程
7. **LLM 降级不阻塞**：同步 10s 超时返 504(5003)；异步 90s 上限后入延迟队列；诊断/告警/LLM 兜底失败均静默降级

---

## 3. 技术栈

| 层级 | 选型 | 备注 |
|------|------|------|
| 前端框架 | Vite 6 + React 19 + TypeScript（strict 全开） | SWC 编译 |
| 前端样式 | Tailwind CSS v4（@theme token） | shadcn/ui 风格自实现 |
| 前端状态 | Zustand 5 + TanStack Query v5 | staleTime 30s |
| 前端路由 | React Router v7 | `createBrowserRouter` + lazy 懒加载 |
| 图谱可视化 | ECharts 5（2D 主）+ react-force-graph-3d（3D 可选） | WebGL2 不可用降级 2D |
| 后端框架 | FastAPI + Pydantic 2 + SQLAlchemy 2.0 (async) | |
| 数据库迁移 | Alembic | 12 个迁移文件 |
| 关系库 | PostgreSQL 15 + pgvector + JSONB | 一份镜像承载三能力 |
| 图数据库 | Neo4j 5 + Cypher + 全文索引（cjk 分词） | 替代 Elasticsearch |
| 缓存/队列 | Redis 7 | db=0 缓存 / db=1 ARQ |
| 异步任务 | ARQ | 并发 10，超时 300s |
| 大模型 | OpenAI 兼容 API（provider 可切换，管理后台 `/admin/llm` 配置） | Instructor + Pydantic 结构化 |
| 数据管线 | Scrapy + Playwright + CDP | SimHash 去重 |
| 认证 | JWT RS256 双 Token + RBAC | access 30min / refresh 7d |
| 容器化 | Docker Compose（5 服务） | api / postgres / redis / neo4j / worker |
| 测试 | PyTest + Vitest + Playwright + Locust | 后端 1168 用例 / 前端 28 测试文件 |
| Python 包管理 | uv | `pyproject.toml` + `uv.lock` |

---

## 4. 目录结构

```
zhigang-compass/
├── README.md                       # 根入口
├── AGENTS.md                       # AI 协作入口（铁律、模块导航）
├── docker-compose.yml              # 5 服务一键部署
├── docs/                           # 项目文档
│   ├── README.md                   # 文档索引
│   ├── design/                     # 设计文档
│   │   ├── 设计文档.md             # 设计文档（单一事实源）
│   │   ├── 执行计划.md             # 执行计划
│   │   └── Code-Wiki.md            # 本文件
│   ├── guides/                     # 开发指南
│   │   ├── 贡献指南.md             # 协作规范
│   │   └── 团队启动指南.md         # 环境配置 + 任务分配
│   └── project/                    # 项目管理
│       ├── 项目概览.md             # 项目定位 + 技术栈
│       └── 进度跟踪.md             # 源码审计进度
├── frontend/                       # 前端工程
│   ├── src/
│   │   ├── app/                    # providers + router
│   │   ├── components/             # layout/ + ui/ + graph/ + match/ + resume/
│   │   ├── routes/                 # 12 个页面 + guards
│   │   ├── store/                  # auth.ts + ui.ts
│   │   ├── lib/                    # api.ts + query-client.ts + utils.ts
│   │   ├── styles/globals.css      # 设计令牌
│   │   └── types/api.d.ts          # OpenAPI 自动生成
│   ├── vite.config.ts
│   └── package.json
└── backend/                        # 后端 Python monorepo
    ├── app/
    │   ├── main.py                 # FastAPI 入口
    │   ├── api/v1/                 # 6 个路由模块（auth/graph/match/resume/evolution/admin）
    │   ├── core/                   # config / database / middleware / security
    │   ├── models/                 # SQLAlchemy 模型（raw/business/base）
    │   ├── schemas/                # APIResponse 统一响应
    │   ├── services/               # 算法引擎（15 个子模块）
    │   │   ├── extraction/         # JD/简历抽取管线
    │   │   ├── kg/                 # 图谱 ID 生成 + 写入 + 聚合
    │   │   ├── matching/           # 人岗匹配
    │   │   ├── evolution/          # 技能演化
    │   │   ├── discovery/          # 新岗位发现
    │   │   ├── graph_algorithms/   # PageRank / Louvain / 最短路径 / 技能簇
    │   │   ├── diagnosis/          # LLM 诊断报告
    │   │   ├── rag/                # RAG 检索
    │   │   ├── embeddings/         # pgvector 存取
    │   │   ├── learning_path/      # 学习路径
    │   │   ├── data_quality/       # 时滞/通胀/SimHash/交叉验证/多样性
    │   │   ├── resume/             # 简历解析（file_parser/pii_mask/reflow）
    │   │   ├── prompts/            # 共享提示词
    │   │   └── alerting.py         # webhook 告警
    │   └── workers/                # ARQ 异步任务（20+ 任务）
    ├── data/crawlers/              # Scrapy 爬虫（13 源，monster 停采）
    ├── configs/                    # 运行时权重 JSON/YAML
    ├── openapi/openapi.yaml        # API 契约（58 paths）
    ├── alembic/                    # 数据库迁移（12 个）
    ├── scripts/                    # 运维/治理/评测脚本（40+）
    ├── tests/                      # 单元/集成/E2E/评测/压测
    └── pyproject.toml
```

---

## 5. 主要模块职责

### 5.1 后端基础设施（[backend/app/core/](../../backend/app/core/)）

| 文件 | 职责 |
|------|------|
| [config.py](../../backend/app/core/config.py) | 配置中心。`Settings(BaseSettings)` 从 `.env` + 环境变量加载；含应用/数据库/JWT/缓存/ARQ/前端目录配置（LLM provider 见 `configs/llm_providers.yaml`）；`is_production` 控制安全开关 |
| [database.py](../../backend/app/core/database.py) | 三库连接管理：PostgreSQL（async engine + session 工厂）、Neo4j（同步 driver）、Redis（async client）；提供 `get_db` / `get_neo4j` / `get_redis` 依赖注入 |
| [middleware.py](../../backend/app/core/middleware.py) | 中间件链：CORS（白名单）+ GZip（>1KB）+ SecurityHeaders（CSP/HSTS/TraceID）+ RateLimitMiddleware（普通 100 req/min / LLM 10 req/min，键 `rate:{ip}:{path}`，Redis 不可用降级放行，错误码 4290） |
| [security.py](../../backend/app/core/security.py) | JWT RS256 双 Token（access 30min / refresh 7d）+ bcrypt 密码哈希 + RBAC 四角色权限映射 |

### 5.2 API 层（[backend/app/api/v1/](../../backend/app/api/v1/)）

[router 聚合](../../backend/app/api/v1/__init__.py) 挂载 6 个子路由到 `/api/v1/*`。**契约 58 paths 全部实现**（openapi.yaml 为单一事实源，前端类型由 `pnpm gen:api` 生成）。

| 子路由 | 前缀 | 端点 | 职责 |
|--------|------|------|------|
| [auth.py](../../backend/app/api/v1/auth.py) | `/auth` | POST `/login` `/logout` `/refresh` `/register` `/password`；GET+PUT `/me` | 认证闭环（双 Token + bcrypt + RBAC） |
| [graph.py](../../backend/app/api/v1/graph.py) | `/graph` | GET `/panorama`（30s Redis 缓存）`/search`（全文检索）`/view/{view_type}` `/position/{id}` `/position/{id}/skills` `/skill/{id}` `/skill/{id}/positions` `/skill/{id}/evidence` `/skill/{id}/courses` `/skill/{id}/prerequisites` `/skill/similar` `/algorithms/pagerank` `/algorithms/shortest-path` `/algorithms/skill-clusters`（**level 参数 + levels 元数据**）`/algorithms/community-tree`（阶段三层级树） | 图谱全景/技能反向查询/证据/学习路径/图算法（PageRank、最短路径、技能簇、社区层级树） |
| [match.py](../../backend/app/api/v1/match.py) | `/match` | POST `/recommend`（ARQ 异步）`/compare` `/feedback`；GET `/task/{task_id}` `/result/{match_id}` `/result/{match_id}/diagnosis` `/result/{match_id}/gap` `/result/{match_id}/path` | 自动推荐 Top-N / 人岗比对 / LLM 诊断报告 / 差距与学习路径 |
| [resume.py](../../backend/app/api/v1/resume.py) | `/resume` | POST `/parse`；GET `/list` `/task/{task_id}`（轮询）`/task/{task_id}/stream`（SSE 进度）`/files/{resume_id}/download`；GET+PUT+DELETE `/{resume_id}` | 简历解析（异步任务）+ 列表/编辑/删除/原文下载 |
| [evolution.py](../../backend/app/api/v1/evolution.py) | `/evolution` | GET `/versions` `/versions/{version_id}` `/diff` `/trends` `/signals` `/state-machine` `/position/{id}/evolution` `/watch` | 图谱版本/对比/趋势/信号/状态机/岗位演化/观察池 |
| [admin.py](../../backend/app/api/v1/admin.py) | `/admin` | users CRUD（`/users` + `/users/{id}`）；`/crawl/status` `/crawl/history` `/crawl/trigger` `/crawl/task/{id}/stream`（SSE 日志）；`/positions/pending` `/positions/{id}/review`（POST approve/reject）`/positions/{id}/archive` `/positions/{position_name}` `/positions/declining`；`/evolution/pending` `/evolution/{id}/review`；`/discovery/watch`；`/audit/logs`（≥180 天）；`/llm-config`（GET/PUT，api_key 打码） | 管理后台：账户/爬取/岗位审核/演化审核/审计/LLM provider 配置，RBAC admin |

统一响应契约 [schemas/common.py](../../backend/app/schemas/common.py)：`APIResponse[T]`（code/msg/data/trace_id）+ `ok()` / `error()` 工具函数。错误码规范见设计文档 §2.4.7（同步超时 5003、限流 4290 等）。

### 5.3 服务层（[backend/app/services/](../../backend/app/services/)）

#### 5.3.1 extraction/ — JD 实体抽取管线

三步管线：LLM Few-Shot 抽取（Instructor/Pydantic 强校验）→ 词典后过滤 → 中文后缀清洗 + 去重。717 条 JD 全量抽取覆盖率 100%。

| 文件 | 关键导出 | 状态 |
|------|---------|------|
| [schemas.py](../../backend/app/services/extraction/schemas.py) | `SkillExtracted` / `ToolExtracted` / `EducationExtracted` / `CertificationExtracted` / `REQUIRESRelation` / `JDExtractionResult` | ✅ 完整 |
| [jd_extractor.py](../../backend/app/services/extraction/jd_extractor.py) | `JDExtractor.extract()`（LLM + 规则兜底 + 后处理编排） | ✅ 完整（LLM 全量上线） |
| [llm_provider.py](../../backend/app/services/extraction/llm_provider.py) | `LLMProvider.extract_structured()` / `call_with_fallback()`（多 provider 重试链） | ✅ 完整 |
| [dictionary.py](../../backend/app/services/extraction/dictionary.py) | `SKILL_ALIAS`（别名表）/ `SKILL_WHITELIST`（白名单）/ `normalize_skill()` / `normalize_position_name(name, skills)`（岗位名归一化，支持技能路由）/ `canonical_skill_name()`（统一归一入口） | ✅ 完整 |
| [post_processor.py](../../backend/app/services/extraction/post_processor.py) | `clean_skill_name()` / `dedup_skills()` / `post_process()` | ✅ 完整 |
| [prompts.py](../../backend/app/services/extraction/prompts.py) | `SYSTEM_PROMPT` / `TASK_TEMPLATE` / `FEW_SHOT_EXAMPLES`（含技能细粒度拆分/薪资提取指令） | ✅ 完整 |

#### 5.3.2 kg/ — 图谱 ID 生成 / 写入 / 聚合

| 文件 | 关键导出 | 状态 |
|------|---------|------|
| [id_generator.py](../../backend/app/services/kg/id_generator.py) | `PREFIX_MAP`（8 类前缀）/ `next_id(tx, entity_type)` | ✅ 完整 |
| [kg_service.py](../../backend/app/services/kg/kg_service.py) | `import_jd` / `import_course`（MERGE 幂等）+ 岗位/技能归一化（传 skills 同步） | ✅ 完整 |
| [aggregation.py](../../backend/app/services/kg/aggregation.py) | `build_aggregates`（Position.freq/REQUIRES.weight+source_count）/ `_inflation_stats` | ✅ 完整 |
| [schema.cypher](../../backend/app/services/kg/schema.cypher) | 节点/关系/索引定义（10 个 UNIQUE 约束 + 4 个 cjk 全文索引），`init_neo4j.py` 建库 | ✅ 完整 |

ID 格式 `{prefix}_{seq:04d}`（如 `sk_0042`），通过 Neo4j Counter 节点原子自增。

#### 5.3.3 matching/ — 人岗匹配引擎

| 文件 | 关键导出 | 状态 |
|------|---------|------|
| [schemas.py](../../backend/app/services/matching/schemas.py) | `Necessity` / `SkillRequirement` / `PositionProfile` / `CandidateProfile` / `MatchRequest` / `MatchResult` | ✅ 完整 |
| [weights.py](../../backend/app/services/matching/weights.py) | `load_weights()`（默认 0.6/0.2/0.2，可被 configs/match_weights.json 覆盖） | ✅ 完整 |
| [engine.py](../../backend/app/services/matching/engine.py) | `MatchEngine.match()` / `RuleBasedMatcher`：三维评分（must/nice/exp）+ CII 通胀修正 + 时效衰减 + **领域维度匹配（词面+语义双路，独立阈值 0.5）** + **熟练度匹配与 must 加权优化** | ✅ 完整 |
| [semantic.py](../../backend/app/services/matching/semantic.py) | paraphrase-multilingual-MiniLM-L12-v2 懒加载 + 依赖注入（不可用降级规则），阈值 Optuna 调优 0.831 | ✅ 完整 |

弱监督黄金集 300 对 Optuna 调优：`w_must=0.673 / w_nice=0.129 / sim=0.831` → **Spearman 0.8808 / Accuracy 0.96**。

#### 5.3.4 evolution/ — 技能演化检测

| 文件 | 关键导出 | 状态 |
|------|---------|------|
| [schemas.py](../../backend/app/services/evolution/schemas.py) | `SkillEvolutionTrend`（5 趋势）/ `EvolutionSignal` / `GraphVersionMeta` | ✅ 完整 |
| [detector.py](../../backend/app/services/evolution/detector.py) | `compute_zscore()` / `classify_trend()` / `EvolutionDetector`（Z-score + MoM + 小基数保护，WindowProvider 注入） | ✅ 完整 |
| [graph_version.py](../../backend/app/services/evolution/graph_version.py) | `GraphVersionManager.create_snapshot()` / `diff_versions()` / `list_versions()`（T+1 自动快照） | ✅ 完整 |

#### 5.3.5 discovery/ — 新岗位发现

**六状态机**：`CANDIDATE → EMERGING → STABLE → DECLINING → ARCHIVED`，另有 `REJECTED` 终态。

| 文件 | 关键导出 | 状态 |
|------|---------|------|
| [schemas.py](../../backend/app/services/discovery/schemas.py) | `PositionState`（6 态）/ `DiscoveryFeatures` / `ConfidenceScore` / `CandidatePosition` | ✅ 完整 |
| [confidence.py](../../backend/app/services/discovery/confidence.py) | `wilson_lower()` / `compute_confidence()`（arxiv/github 2σ 加分：单 +0.10 / 双 +0.15） | ✅ 完整 |
| [detector.py](../../backend/app/services/discovery/detector.py) | `passes_gate()` / `passes_cold_start_gate()` / `DiscoveryDetector`（z-score 窗口 3 周，冷启动门槛已调降） | ✅ 完整 |
| [state_machine.py](../../backend/app/services/discovery/state_machine.py) | `VALID_TRANSITIONS` / `PositionStateMachine.transition()`（自动转换判定 + Neo4j 持久化，含 `freq_z_scores()` 回迁修复） | ✅ 完整 |
| [grounding.py](../../backend/app/services/discovery/grounding.py) | `search_authoritative()` RAG 接地双路（Neo4j 全文 + pgvector 语义，Neo4j 不可达降级 ILIKE）/ `_generate_definition`（LLM 中文凝练，失败静默回退） | ✅ 完整 |

#### 5.3.6 graph_algorithms/ — 图算法应用（设计文档 §7.1，M4→M5 过渡批次落地）

| 文件 | 关键导出 | 状态 |
|------|---------|------|
| [pagerank.py](../../backend/app/services/graph_algorithms/pagerank.py) | PageRank 技能重要性排序：幂迭代收敛（阻尼 0.85，MAX_ITER 50，TOL 1e-6），无向共现边按双有向边聚合；纯计算不依赖 Neo4j | ✅ 完整 |
| [louvain.py](../../backend/app/services/graph_algorithms/louvain.py) | Louvain 技能簇识别：标准两阶段（模块度增量 ΔQ 最大化 + 社区聚合重跑）。**阶段一（G-04）γ 分辨率参数化**：`louvain(graph, resolution=1.0)` 增益/模块度 resolution 化（γ>1 细簇 / γ<1 粗簇 / 1.0 等价标准 Louvain 向后兼容）；新增 `homogeneity()` 加权簇内同质性（Optuna objective 0.3 权重项）；**阶段三层次化提取**：`louvain_hierarchical()` 收集全部层级（{levels, best_level, membership}，level 0 最细，best 与 louvain() 一致） | ✅ 完整 |
| [config.py](../../backend/app/services/graph_algorithms/config.py) | 图算法运行时配置加载（`configs/graph_algo.yaml`：algorithm/resolution/min_weight/min_size，缺失回退默认；Optuna 最优参数随配置生效） | ✅ 完整 |
| [leiden.py](../../backend/app/services/graph_algorithms/leiden.py) | **阶段二 Leiden 条件替换**：同签名 `leiden(graph, resolution=1.0)`（igraph 1.0 + leidenalg 0.12，seed=0 确定性）；验收未达标（Q 落后 0.037），默认保持 louvain，双实现并存配置一行切换；API 依赖缺失自动回退 | ✅ 完整（未启用） |
| [shortest_path.py](../../backend/app/services/graph_algorithms/shortest_path.py) | 技能最短路径：Neo4j 核心 `shortestPath((:Skill)-[*..6]-)`，沿岗位共现 + REQUIRES 边走，返回节点序列由前端按 type 区分展示 | ✅ 完整 |
| [cluster_llm.py](../../backend/app/services/graph_algorithms/cluster_llm.py) | 技能簇 LLM 兜底：`ClusterLLMDecision`（JSON Schema 强校验）+ `build_cluster_prompt` + `classify_cluster`（失败降级规则标签不阻塞） | ✅ 完整 |
| [postprocess.py](../../backend/app/services/graph_algorithms/postprocess.py) | 技能簇后处理：规则优先后处理 + LLM 兜底触发判断纯函数化（是否调 LLM 由 API 层决定，本模块只输出触发标记，保持可单测/可缓存） | ✅ 完整 |
| [network.py](../../backend/app/services/graph_algorithms/network.py) | 技能共现网络加载（`load_skill_cooccurrence`），供 PageRank/Louvain 消费 | ✅ 完整 |

#### 5.3.7 diagnosis/ — LLM 诊断报告（设计文档 §9.5）

| 文件 | 关键导出 | 状态 |
|------|---------|------|
| [generator.py](../../backend/app/services/diagnosis/generator.py) | `generate_diagnosis()`：匹配结果 + 差距 + 学习路径 + 图谱上下文 → LLM 结构化报告；`call_with_fallback` 多 provider 降级，全失败由 API 层映射 503/504（不阻断匹配主流程） | ✅ 完整 |
| [schemas.py](../../backend/app/services/diagnosis/schemas.py) | 诊断报告 Pydantic Schema（JSON Schema 强校验） | ✅ 完整 |
| [prompts.py](../../backend/app/services/diagnosis/prompts.py) | 诊断提示词 | ✅ 完整 |

#### 5.3.8 rag/ + embeddings/ + alerting/ + prompts/

| 文件 | 关键导出 | 状态 |
|------|---------|------|
| [rag/retrieval.py](../../backend/app/services/rag/retrieval.py) | 通用 RAG 检索（§6.4）：图谱岗位定义（discovery_candidates + occupations 三源权威定义）+ 技能全文（skill_search）+ 历史诊断报告（diagnosis_reports）三源动态检索 | ✅ 完整 |
| [embeddings/vector_store.py](../../backend/app/services/embeddings/vector_store.py) | pgvector 存取与消费辅助（§11.4.3）：`load_*` 按业务键映射 `{key: vector}`，供 skill/similar、dedup_simhash 语义辅助、engine._project_score 消费 | ✅ 完整 |
| [embeddings/backfill.py](../../backend/app/services/embeddings/backfill.py) | embedding 回填任务入口 | ✅ 完整 |
| [alerting.py](../../backend/app/services/alerting.py) | webhook 告警（§4.4/§11.1）：兼容飞书/钉钉/企微机器人 POST JSON；未配置或失败仅记日志不阻塞主流程 | ✅ 完整 |
| [prompts/](../../backend/app/services/prompts/) | 共享提示词包（soft_skill 等跨模块复用） | ✅ 完整 |

### 5.4 异步任务（[backend/app/workers/](../../backend/app/workers/)）

| 文件 | 职责 |
|------|------|
| [tasks.py](../../backend/app/workers/tasks.py) | 20+ ARQ 任务 + `WorkerSettings`（`functions=[...]` 注册清单）：**ETL 管线** `run_etl_pipeline`（11 阶段编排：采集→清洗→抽取→入图→聚合→交叉验证→课程评估→归一化→多样性→新鲜度）+ `crawl_platform`（爬取 trigger，city 参数）+ `batch_extract`（JD 批量抽取，N 条/批一次 LLM 调用，3.2x 提速）+ `resume_parse` + `match_recommend` + `snapshot_graph`（T+1 自动快照）+ `discovery_daily`（候选池 + RAG 接地）+ `discovery_auto_transition`（状态自动流转）+ `watch_signal_daily` + `validate_temporal` / `detect_inflation` / `dedup_simhash` / `load_courses` / `evaluate_courses` / `diversity_report` / `check_data_freshness` / `aggregate_positions` / `cross_validate_jds` / `sync_skill_normalization` / `backfill_embeddings` / `check_llm_providers_health` + `on_startup` / `on_shutdown` |

`WorkerSettings` 定义于 [tasks.py](../../backend/app/workers/tasks.py)（L2181）：Redis db=1（与缓存隔离）、并发 10、超时 300s、重试 2 次。任务级 `job_timeout` 按函数配置（坑 22）。

调度入口 [scripts/cron/](../../backend/scripts/cron/)：`etl_daily.py` / `crawl_spider.py` / `discovery_daily.py`（每日 05:30 入队） / `snapshot_daily.py` + `crontab.example` / `scheduled_tasks.ps1`。

### 5.5 数据管线（[backend/data/crawlers/](../../backend/data/crawlers/)）

Scrapy + Playwright + CDP，13 源（7 招聘 A/B/C 三级分级 + 6 非招聘：课程/论文/社区）。**monster 已停采**（08.06，DataDome 不可绕过，代码保留待有住宅代理/指纹浏览器后启用）；BOSS 已重构为 HTTP 采集（cookies 模式）+ 容器内采集；glassdoor/maimai 支持容器内采集。

**招聘数据源**（BOSS / 智联 / Indeed / LinkedIn / Glassdoor / 脉脉 已贯通，monster 停采）：CDP 爬虫采用 **「Scrapy Spider + 独立脚本 + subprocess 隔离」** 架构，避免 Playwright(asyncio) / JobSpy(同步) 与 Scrapy Twisted 事件循环冲突。

**非招聘数据源**：6 个爬虫直接继承 `Scrapy.Spider`（非 `BaseSpider`），构造 `CourseItem` / `PaperItem` / `CommunityTrendItem`：
- 课程 3 个（icourse163/Coursera/edX）：Playwright 渲染搜索页，用于构建 `(:Skill)-[:LEARNABLE_VIA]->(:Course)` 关系
- 论文 1 个（arXiv）：官方 API + Atom XML 解析，进技术热点观察池
- 社区 2 个（GitHub Trending/Stack Overflow）：公开页 HTML 解析，进技术热点观察池
- **分层源策略**（设计文档 §7.2.2）：arXiv/GitHub/SO 信号不独立触发 candidate，仅作 candidate→emerging 阶段置信度加分（单异常 +0.10 / 双异常 +0.15，封顶 1.0）

| 文件 | 职责 |
|------|------|
| [base_spider.py](../../backend/data/crawlers/base_spider.py) | `BaseSpider` 基类：关键字 × 城市遍历 + `make_item()` 统一构造 + 脉脉合规头（仅招聘源继承） |
| [items.py](../../backend/data/crawlers/items.py) | `_BaseItem` 基类 + 4 子类：`JobItem`（招聘）/ `CourseItem`（课程）/ `PaperItem`（论文）/ `CommunityTrendItem`（社区趋势） |
| [pipelines.py](../../backend/data/crawlers/pipelines.py) | `CleaningPipeline`（SHA256 指纹 + 脉脉脱敏 + 文本标准化 + **SimHash 语义指纹** + **长度过滤 <50 字 + 质量评分（字段完整度 0.4/文本长度 0.3/核心词 0.2/格式 0.1，<0.6 标 needs_review）+ 时效加权（≤30 天 1.0，>30 天 exp 衰减）** + **post_date 多源格式归一化**）→ `PostgresPipeline`（按 Item 类型路由 upsert 到 4 张 raw 表，数据库不可用降级 JSONL） |
| [middlewares.py](../../backend/data/crawlers/middlewares.py) | `UARotationMiddleware`(400) + `ProxyPoolMiddleware`(410，失败剔除) + `ExponentialBackoffMiddleware`(420) |
| [scrapy_settings.py](../../backend/data/crawlers/scrapy_settings.py) | 框架设置：Playwright handler + AsyncioSelectorReactor + JSONL Feed |
| [settings.py](../../backend/data/crawlers/settings.py) | 业务配置：招聘平台分级 / 非招聘数据源速率限制 / 代理池 / 脉脉合规 / 采集量目标 |
| [setup_boss_chrome.py](../../backend/data/crawlers/setup_boss_chrome.py) | ✅ 隔离 Chrome 启动脚本（CDP 9222）：启动带真实指纹的 Chrome/Edge，用户手动登录后持久保存登录态。BOSS/Glassdoor/Maimai 共用 |
| [boss_cdp_crawler.py](../../backend/data/crawlers/boss_cdp_crawler.py) | ✅ BOSS 独立采集脚本：CDP 连接 + 内部 API `/wapi/zpgeek/search/joblist.json`，输出 JSONL。参考 [eatmoreduck/boss-zhipin-scraper](https://github.com/eatmoreduck/boss-zhipin-scraper) |
| [monster_cdp_crawler.py](../../backend/data/crawlers/monster_cdp_crawler.py) | ⏸ 停采（DataDome 不可绕过）。CDP + XHR 拦截 appsapi.monster.io，参考 [shahidirfan100/Monster-Job-Scraper](https://github.com/shahidirfan100/Monster-Job-Scraper) |
| [jobspy_crawler.py](../../backend/data/crawlers/jobspy_crawler.py) | ✅ JobSpy 共享采集脚本：被 spiders/indeed.py、spiders/linkedin_public.py 通过 subprocess 调用（`python-jobspy` 解析 JSON-LD），输出 JSONL 到 stdout，避免与 Scrapy Twisted 事件循环冲突。参考 [speedyapply/JobSpy](https://github.com/speedyapply/JobSpy) |
| [glassdoor_cdp_crawler.py](../../backend/data/crawlers/glassdoor_cdp_crawler.py) | ✅ Glassdoor 独立采集脚本：CDP 连接 + SSR DOM 提取（JSON-LD ItemList + data-test 属性双兜底），绕过 Cloudflare；容器内 headless 采集（容器内实测 item_scraped_count=58） |
| [maimai_cdp_crawler.py](../../backend/data/crawlers/maimai_cdp_crawler.py) | ✅ 脉脉独立采集脚本：CDP 连接 + 飞书招聘页 DOM 提取（maimai.jobs.feishu.cn，无需登录态） |
| [spiders/arxiv.py](../../backend/data/crawlers/spiders/arxiv.py) | ✅ arXiv 论文爬虫：官方 API + Atom XML 解析，默认拉取 7 个 cs.* 分类，产出 `PaperItem` |
| [spiders/github.py](../../backend/data/crawlers/spiders/github.py) | ✅ GitHub Trending 爬虫：公开页 HTML 解析 `article.Box-row`，提取 star/fork/language/stars_today，产出 `CommunityTrendItem` |
| [spiders/stackoverflow.py](../../backend/data/crawlers/spiders/stackoverflow.py) | ✅ Stack Overflow 爬虫：标签页 HTML 解析 `div.s-post-summary`，提取 votes/views/answers/tags，产出 `CommunityTrendItem` |
| [spiders/icourse163.py](../../backend/data/crawlers/spiders/icourse163.py) | ✅ 中国大学MOOC 爬虫：Playwright 渲染搜索页，解析课程卡片，产出 `CourseItem`（国内直连；过滤专升本/期末培训课） |
| [spiders/coursera.py](../../backend/data/crawlers/spiders/coursera.py) | ✅ Coursera 爬虫：Playwright 渲染搜索页，解析 `li.cds-grid-item` 卡片，产出 `CourseItem`（需代理） |
| [spiders/edx.py](../../backend/data/crawlers/spiders/edx.py) | ✅ edX 爬虫：Playwright 渲染搜索页，解析 `div.d-card-wrapper` 卡片，产出 `CourseItem`（需代理） |
| [spiders/](../../backend/data/crawlers/spiders/) | 13 源（12 自动采集 + monster 停采）：boss ✅ / zhilian ✅（详情正文补抓）/ monster ⏸ 停采 / indeed ✅ / glassdoor ✅ / maimai ✅ / linkedin_public ✅ / arxiv ✅ / github ✅ / stackoverflow ✅ / icourse163 ✅ / coursera ✅ / edx ✅ |

### 5.6 前端工程（[frontend/](../../frontend/)）

| 模块 | 职责 |
|------|------|
| [main.tsx](../../frontend/src/main.tsx) + [app/providers.tsx](../../frontend/src/app/providers.tsx) | 入口 + Provider 链（QueryClientProvider → AppRouter） |
| [app/router.tsx](../../frontend/src/app/router.tsx) | `createBrowserRouter`，12 页 lazy 懒加载 + AuthGuard/GuestGuard + RBAC |
| [components/layout/](../../frontend/src/components/layout/) | AppShell / TopNav / Sidebar / PageHeader / PagePlaceholder / CompassMark（签名 SVG） |
| [components/ui/](../../frontend/src/components/ui/) | shadcn 风格基元：Button / Card / Badge（含五状态色）/ Input |
| [components/graph/](../../frontend/src/components/graph/) | 图谱组件族：graph-2d（ECharts 力导向）/ graph-3d（react-force-graph-3d，聚焦/重置视角）/ graph-analysis-panel（算法分析面板）/ node-detail-panel（节点详情）/ use-graph-pan（拖拽平移）/ graph-layout / graph-utils / types |
| [components/match/](../../frontend/src/components/match/) | 匹配结果展示组件（得分/差距/学习路径/诊断报告） |
| [components/resume/](../../frontend/src/components/resume/) | resume-uploader（上传组件，白名单与后端一致） |
| [routes/](../../frontend/src/routes/) | 12 页面 + guards.tsx：dashboard / graph（2D+3D 图谱）/ evolution（演化看板）/ resume-match（简历匹配）/ profile（个人中心）/ login / register / admin-dashboard / admin-users / admin-crawl / admin-review（岗位审核）/ admin-llm（LLM provider 配置） |
| [store/auth.ts](../../frontend/src/store/auth.ts) | 认证 store（不存 token，token 走 httpOnly Cookie + 内存） |
| [store/ui.ts](../../frontend/src/store/ui.ts) | UI store（sidebar 开关 + 主题切换 + localStorage 持久化） |
| [lib/api.ts](../../frontend/src/lib/) | axios 实例 + 401 静默续期拦截器（refresh_token 队列） |
| [lib/query-client.ts](../../frontend/src/lib/) | TanStack Query 配置（staleTime 30s，4xx 不重试） |
| [styles/globals.css](../../frontend/src/styles/globals.css) | Tailwind v4 @theme 设计令牌 + 五状态色 + 深色模式 |
| [types/api.d.ts](../../frontend/src/types/api.d.ts) | openapi-typescript 自动生成（58 paths 类型） |

---

## 6. 关键类与函数说明

### 6.1 配置与安全

#### `Settings` — [core/config.py](../../backend/app/core/config.py)
应用配置中心，`pydantic-settings` 驱动。
- 关键字段：`postgres_dsn` / `neo4j_uri` / `redis_url` / `jwt_*` / `arq_*`（LLM provider 配置见 `configs/llm_providers.yaml`，可配置任意 OpenAI 兼容 API）
- 关键 property：`is_production`（控制 CORS/HSTS/Swagger/SECRET_KEY 守卫）、`jwt_private_key` / `jwt_public_key`（惰性读文件）

#### `create_access_token(user_id, role)` / `create_refresh_token(user_id)` — [core/security.py](../../backend/app/core/security.py)
JWT RS256 签发。Access 含 `sub/role/type=access`，30min；Refresh 含 `sub/type=refresh`，7d。

#### `has_permission(role, permission)` — [core/security.py](../../backend/app/core/security.py)
RBAC 校验。`admin` 持 `{"*"}` 全权；`editor`/`viewer`/`guest` 各持细粒度权限集。

### 6.2 抽取管线

#### `JDExtractor.extract(jd_text)` — [extraction/jd_extractor.py](../../backend/app/services/extraction/jd_extractor.py)
JD 抽取主入口。文本 < 10 字符返回空结果；否则 LLM 抽取（Instructor 强校验 + 多 provider 重试链），失败 fallback 到 `_rule_based_extract`（白名单字符串匹配）；最后 `post_process`。

#### `post_process(result)` — [extraction/post_processor.py](../../backend/app/services/extraction/post_processor.py)
后处理管线：① `normalize_skill` 别名归一化 ② `clean_skill_name` 中文后缀清洗（24 个后缀）③ `dedup_skills` 去重 ④ requirements 归一化。

#### `normalize_skill(raw)` / `normalize_position_name(name, skills)` — [extraction/dictionary.py](../../backend/app/services/extraction/dictionary.py)
技能归一：`strip` + 查 `SKILL_ALIAS` 别名表（如 `JS→JavaScript`、`k8s→Kubernetes`）。岗位归一：同义合并 + 英文翻译 + **兜底族按技能路由**（`_GENERIC_ROUTED_FAMILIES` 拦截后由 `_POSITION_SKILL_ROUTING` 按 JD 技能路由到细分族，无技能或未命中返回空串不入图）。

#### `canonical_skill_name(raw)` — [extraction/dictionary.py](../../backend/app/services/extraction/dictionary.py)
岗位/技能归一化统一入口（08.08 治理收敛），全链路经此规范化。

### 6.3 匹配引擎

#### `load_weights()` — [matching/weights.py](../../backend/app/services/matching/weights.py)
加载 `(w_must, w_nice, w_exp)`，默认 `(0.6, 0.2, 0.2)`。读 `configs/match_weights.json`，文件缺失/异常回退默认值（不抛异常）。

#### `MatchEngine.match(request)` — [matching/engine.py](../../backend/app/services/matching/engine.py)
匹配接口：① 倒排索引粗筛 Top-200 ② 三维评分（must/nice/exp）+ CII 通胀修正 + 时效衰减 + 领域维度（词面+语义双路）+ 软技能降权（low_confidence ×0.5）+ 熟练度匹配 ③ total DESC 截 Top-N。
- 缺必备技能比例惩罚：`must_penalty = 1 - (missing/total) × 0.3`
- 加分技能空集保护：`if len(nice_skills) == 0: nice_score = 1.0`
- 语义增强：`semantic.py` SBERT sim≥0.831 计入命中（Optuna 调优）

#### `MatchEngine.compare(request)` — [matching/engine.py](../../backend/app/services/matching/engine.py)
单点比对，附带 gaps（三态差距分析）/ learning_path / 证据引用（MENTIONED_IN 链路）/ 诊断报告（`/match/result/{id}/diagnosis`）。

### 6.4 演化检测

#### `compute_zscore(current, mean, std)` — [evolution/detector.py](../../backend/app/services/evolution/detector.py)
`(f(t) - μ) / σ`，`std=0` 返回 `0.0`。

#### `classify_trend(z_score, current_freq, protected=False)` — [evolution/detector.py](../../backend/app/services/evolution/detector.py)
按阈值判定 5 趋势：
- `protected=True` 或频次 < 10 → `PROTECTED`
- `z > 2.0` → `EMERGING`；`z > 1.5` → `RISING`；`z < -1.5` → `DECLINING`；否则 `STABLE`

### 6.5 新岗位发现

#### `wilson_lower(successes, total, z=1.96)` — [discovery/confidence.py](../../backend/app/services/discovery/confidence.py)
Wilson score 95% 置信区间下界，冷启动（< 60 天历史）兜底。

#### `compute_confidence(jd_count, source_count, growth_rate, arxiv_anomaly, github_anomaly)` — [discovery/confidence.py](../../backend/app/services/discovery/confidence.py)
综合置信度：`w_count·norm(jd_count) + w_source·norm(source_count) + w_growth·norm(growth_rate)`（默认 0.4/0.3/0.3）。单异常 +0.10，双异常 +0.15，封顶 1.0。

#### `passes_gate(features, history_days)` / `passes_cold_start_gate(...)` — [discovery/detector.py](../../backend/app/services/discovery/detector.py)
candidate 门控：正常 `z>2.0 AND source_diversity≥2 AND jd_freq_ma3≥10`（严格）或 `z>1.5 AND source_diversity≥2`（保守）；冷启动走 Wilson score > 0.3。z-score 窗口 3 周，存量成熟岗位自动排除。

#### `PositionStateMachine.transition(candidate, target_state, operator, reason)` — [discovery/state_machine.py](../../backend/app/services/discovery/state_machine.py)
状态转换：合法性校验（查 `VALID_TRANSITIONS`，非法抛 `ValueError`）+ Neo4j 持久化。`freq_z_scores()` 重建序列 z-score，修复 declining→stable 回迁。

### 6.6 图算法

#### `pagerank(adjacency)` — [graph_algorithms/pagerank.py](../../backend/app/services/graph_algorithms/pagerank.py)
PageRank 技能重要性：幂迭代（阻尼 0.85），无向共现边按双有向边聚合入边；纯计算模块便于单测。

#### `louvain(graph, resolution=1.0)` — [graph_algorithms/louvain.py](../../backend/app/services/graph_algorithms/louvain.py)
Louvain 技能簇：两阶段模块度优化（节点移动 ΔQ 最大化 → 社区聚合迭代）。**γ 分辨率参数化（阶段一）**：增益 `ΔQ = k_in/m − γ·(Σ_tot·k_i)/(2m²)`；γ>1 细簇 / γ<1 粗簇 / 1.0 等价标准 Louvain（默认向后兼容）。

#### `homogeneity(graph, partition)` — [graph_algorithms/louvain.py](../../backend/app/services/graph_algorithms/louvain.py)
加权簇内同质性：Σ_c 簇内边权重 / Σ_c (簇内+簇间) 边权重，值域 [0,1]。Optuna objective 0.3 权重项。

#### `load_graph_algo_config()` — [graph_algorithms/config.py](../../backend/app/services/graph_algorithms/config.py)
加载 `configs/graph_algo.yaml`（algorithm/resolution/min_weight/min_size），缺失/解析失败回退默认值（不抛错）。**API 默认参数随配置生效**；skill-clusters 缓存键含 resolution（γ 变更不串缓存）。

#### `leiden(graph, resolution=1.0)` — [graph_algorithms/leiden.py](../../backend/app/services/graph_algorithms/leiden.py)
阶段二 Leiden 条件替换：同签名（igraph 1.0 + leidenalg 0.12，RBConfigurationVertexPartition + resolution_parameter，seed=0 确定性），输出与 louvain 同格式（reindex 0..k-1）。**2026-08-12 验收未达标**（同质性 +0.21 领先但 Q −0.037 落后，两项需同时达标），默认 `algorithm=louvain` 不切换；configs 一行切换 + API 依赖缺失自动回退 louvain。

#### 参数调优 — [scripts/graph_algo_tune.py](../../backend/scripts/graph_algo_tune.py)
图算法阶段一 Optuna 扫描：γ∈[0.5,2.0] × min_weight∈[1.0,3.0]，objective = 0.5·Q + 0.3·同质性 + 0.2·(1−过小簇占比)。Q 用标准模块度评分（γ 只生成划分），**退化解（簇数 ≤2 或最大簇占比 >0.5）罚 0**（2026-08-12 实跑修复 γ<1 单簇退化最优）。2026-08-12 真实快照（386 节点/3901 边）50 trial 最优 γ=1.256 / min_weight=2.502（同质性 0.37→0.56，详见 [图算法参数评估报告.md](./图算法参数评估报告.md)）。模式：--export（Neo4j 快照导出）/ --snapshot（固定数据集扫描）/ --dry-run（当前配置指标）/ --apply（写回 configs）/ **--compare（阶段二 Leiden 验收对比）**/ **--algorithm {louvain,leiden}（Leiden 专属调优——参数不可互通，须自身空间扫描）**。

#### `shortest_path(session, from_skill, to_skill)` — [graph_algorithms/shortest_path.py](../../backend/app/services/graph_algorithms/shortest_path.py)
Neo4j 核心 `shortestPath`（深度 ≤6，沿「岗位共现 + REQUIRES」边，可经 Position 节点），失败返回 None。

#### `classify_cluster(...)` / `postprocess_clusters(...)` — [graph_algorithms/cluster_llm.py](../../backend/app/services/graph_algorithms/cluster_llm.py) / [postprocess.py](../../backend/app/services/graph_algorithms/postprocess.py)
技能簇后处理：规则优先（§4.1 按序执行）+ LLM 兜底（触发判断纯函数化，`ClusterLLMDecision` JSON Schema 强校验，失败降级规则标签不阻塞）。

### 6.7 图谱 ID 生成

#### `next_id(tx, entity_type)` — [kg/id_generator.py](../../backend/app/services/kg/id_generator.py)
通过 Neo4j Counter 节点原子自增，返回 `{prefix}_{seq:04d}`。8 类前缀：`pos/sk/ev/co/oc/ce/ed/tl`。

### 6.8 前端关键模块

#### `AppRouter` — [app/router.tsx](../../frontend/src/app/router.tsx)
`createBrowserRouter` 三类路由：公开（GuestGuard）/ 受保护（AuthGuard）/ 管理员（requireRole=['admin']）。12 页 `lazy()` 懒加载。

#### `apiClient` + 401 拦截器 — [lib/api.ts](../../frontend/src/lib/)
axios 实例（`withCredentials`，30s）。401 触发 `POST /auth/refresh`，期间其他 401 入队等待，成功重放，失败跳 `/login`。

#### `useAuthStore` / `useUIStore` — [store/](../../frontend/src/store/)
- auth：`user/isAuthenticated/setUser/logout/hasPermission`，**不存 token**
- ui：`sidebarOpen/theme/toggleTheme`，主题持久化到 `localStorage['zhigang-theme']`

---

## 7. 依赖关系

### 7.1 后端内部依赖

```
app/main.py
  ├─ app.core.config.settings
  ├─ app.core.middleware.setup_middleware
  └─ app.api.v1.router
        ├─ auth.py    → core.security (JWT/bcrypt/RBAC), schemas.common
        ├─ graph.py   → core.database.get_neo4j, services.graph_algorithms, schemas.common
        ├─ match.py   → workers.tasks.match_recommend, services.matching, services.diagnosis
        ├─ resume.py  → workers.tasks.resume_parse, services.resume
        ├─ evolution.py → services.evolution, services.discovery
        └─ admin.py   → models.business, workers.tasks.crawl_platform, core.security (RBAC)

core.database → core.config.settings
core.security → core.config.settings
workers.config → core.config.settings

services.extraction:
  prompts.py ──┐
  llm_provider.py ─┤
                jd_extractor.py ──> post_processor.py ──> dictionary.py
                                          └──> schemas.py（被所有文件依赖）

services.discovery:
  schemas.py ──┬─> state_machine.py
              ├─> confidence.py
              └─> detector.py ──> confidence.py
                    └─> grounding.py（RAG 双路检索）

services.matching:
  schemas.py ← engine.py ← weights.py（+ configs/match_weights.json）
                    └─> semantic.py（SBERT 语义增强）

services.graph_algorithms:
  network.py ──> pagerank.py / louvain.py（纯计算）
  postprocess.py ──> cluster_llm.py（LLM 兜底，触发由 API 层决定）

services.diagnosis:
  generator.py ──> services.rag.retrieval（图谱上下文检索）
```

### 7.2 配置文件依赖

| 代码 | 配置文件 |
|------|---------|
| [matching/weights.py](../../backend/app/services/matching/weights.py) | [configs/match_weights.json](../../backend/configs/match_weights.json)（Optuna 调优：0.673/0.129） |
| [discovery/confidence.py](../../backend/app/services/discovery/confidence.py) | [configs/discovery_weights.json](../../backend/configs/discovery_weights.json) |
| [discovery/detector.py](../../backend/app/services/discovery/detector.py) | [configs/emerging_seeds.yaml](../../backend/configs/emerging_seeds.yaml)（种子列表） |
| [data_quality/inflation_detector.py](../../backend/app/services/data_quality/inflation_detector.py) | [configs/inflation_weights.json](../../backend/configs/inflation_weights.json)（Optuna 调优） |
| [learning_path](../../backend/app/services/learning_path/) | [configs/skill_prerequisites.yaml](../../backend/configs/skill_prerequisites.yaml)（40+ 技能先修字典） |
| LLM Provider | [configs/llm_providers.yaml](../../backend/configs/llm_providers.yaml)（单一事实源，api_key 由管理后台写入，gitignore 忽略） |

### 7.3 前端依赖

```
main.tsx → app/providers.tsx (QueryClientProvider) → app/router.tsx
                                                              ├─ components/layout/app-shell.tsx
                                                              │     ├─ top-nav.tsx
                                                              │     ├─ sidebar.tsx ← nav-config.ts
                                                              │     └─ page-header.tsx / page-placeholder.tsx
                                                              ├─ routes/*.tsx (12 页 lazy) + guards.tsx
                                                              │     ├─ components/graph/*（2D/3D/分析面板/节点详情）
                                                              │     ├─ components/match/* + components/resume/*
                                                              │     ├─ components/ui/* + store/auth.ts + store/ui.ts
                                                              │     └─ lib/api.ts (axios + 401 拦截) + lib/query-client.ts
                                                              └─ types/api.d.ts ← openapi-typescript(../backend/openapi/openapi.yaml)
```

### 7.4 外部服务依赖

| 服务 | 用途 | 端口 |
|------|------|------|
| PostgreSQL 15 + pgvector | 关系数据 + 向量检索 + JSONB 快照 + 权威库（occupations） | 5432 |
| Neo4j 5 | 能力图谱 + 全文索引（cjk） | 7687 (Bolt) / 7474 (Browser) |
| Redis 7 | 缓存（db=0）+ ARQ 队列（db=1）+ 限流 | 6379 |
| LLM Provider | 任意 OpenAI 兼容 API（讯飞星火 / DeepSeek / Qwen 等，运行时切换） | HTTPS |

### 7.5 Python 依赖（[pyproject.toml](../../backend/pyproject.toml)）

核心：`fastapi` / `uvicorn[standard]` / `pydantic` / `pydantic-settings` / `sqlalchemy[asyncio]` / `asyncpg` / `alembic` / `pgvector` / `neo4j` / `redis` / `httpx` / `arq` / `pyjwt[crypto]` / `passlib[bcrypt]` / `instructor` / `openai` / `sentence-transformers` / `pypdf` / `pdfplumber` / `paddleocr`（扫描件 OCR）。
开发：`pytest` / `pytest-asyncio` / `pytest-cov` / `locust` / `ruff`。
爬虫：`scrapy` / `scrapy-playwright` / `playwright` / `python-jobspy`。

---

## 8. 项目运行方式

### 8.1 环境要求

- Node.js ≥ 20.10 + pnpm 9
- Python ≥ 3.11 + uv（推荐）
- Docker Desktop ≥ 24.0 + Docker Compose v2

### 8.2 一键启动（Docker，生产形态）

```bash
# 1. 克隆仓库
git clone <repo-url>
cd zhigang-compass

# 2. 先构建前端静态资源（FastAPI 同端口托管）
cd frontend && pnpm install && pnpm build && cd ..

# 3. 配置环境变量
cp backend/.env.example backend/.env   # 填入 LLM API Key、SECRET_KEY 等
cp backend/configs/llm_providers.yaml.example backend/configs/llm_providers.yaml

# 4. 一键启动 5 服务（api / postgres / redis / neo4j / worker）
docker compose up -d

# 5. 健康检查
curl http://localhost:8000/health     # → {"status":"healthy"}
```

### 8.3 本地开发（前后端分离）

```bash
# 终端 1：基础设施
docker compose up -d postgres redis neo4j

# 终端 2：后端
cd backend
uv sync
uv run alembic upgrade head          # 数据库迁移（12 个）
uv run python scripts/init_neo4j.py  # Neo4j 建库（约束 + 索引）
uv run uvicorn app.main:app --reload --port 8000

# 终端 3：前端
cd frontend
pnpm install
pnpm dev                             # Vite dev server，/api 反代到 8000
```

### 8.4 数据采集

```bash
cd backend/data/crawlers
uv run playwright install chromium

# === 前置：CDP 浏览器（BOSS / Glassdoor / Maimai 共用，端口 9222）===
python -m crawlers.setup_boss_chrome            # 启动隔离 Chrome/Edge
# 在弹出的浏览器中：登录 zhipin.com（BOSS）
python -m crawlers.setup_boss_chrome --check    # 检查 CDP + 登录态

# === BOSS（需 CDP 浏览器保持开启）===
scrapy crawl boss -a keywords=Python -a cities=北京 -o output/boss.jsonl

# === 智联招聘（直连）===
scrapy crawl zhilian -a keywords=Python -a cities=北京 -o output/zhilian.jsonl

# === Indeed（需 Clash/V2Ray 代理）===
$env:HTTPS_PROXY="http://127.0.0.1:7890"
scrapy crawl indeed -a keywords=Python -a cities="New York" -o output/indeed.jsonl

# 采集完成后关闭 CDP 浏览器
python -m crawlers.setup_boss_chrome --stop
```

详见 [crawlers/README.md](../../backend/data/crawlers/README.md)。

### 8.5 测试与评测

```bash
# 后端测试（Windows 下加 --basetemp=.pytest_tmp 绕开系统临时目录）
cd backend && uv run pytest --cov=app --basetemp=.pytest_tmp

# 前端测试与类型检查
cd frontend && pnpm test && pnpm typecheck

# 重新生成前端 API 类型（契约变更后）
cd frontend && pnpm gen:api

# 准确率评测（统一入口）
cd backend && uv run python scripts/evaluate.py --task all
```

### 8.6 关键环境变量（[backend/.env.example](../../backend/.env.example)）

```bash
APP_ENV=development                  # development | production
SECRET_KEY=<生产必须改>

# 数据库
POSTGRES_DSN=postgresql+asyncpg://zhigang:zhigang@localhost:5432/zhigang
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
REDIS_URL=redis://localhost:6379/0

# LLM provider（任意 OpenAI 兼容 API）：
#   组合/优先级/模型/api_key 全部在 configs/llm_providers.yaml（已 gitignore，
#   api_key 由管理后台 /admin/llm 填写落盘，不入 env）。全新 clone 后复制
#   configs/llm_providers.yaml.example 为 llm_providers.yaml 即可。

# 前端
VITE_API_TARGET=http://localhost:8000 # Vite 开发代理目标

# 告警（可选）
ALERT_WEBHOOK_URL=                   # 飞书/钉钉/企微机器人 webhook，未配置仅记日志
```

### 8.7 常用命令速查

| 场景 | 命令 |
|------|------|
| 启动开发环境 | `docker compose up -d` |
| 后端热重载 | `cd backend && uv run uvicorn app.main:app --reload` |
| 前端开发 | `cd frontend && pnpm dev` |
| 数据库迁移 | `cd backend && uv run alembic upgrade head` |
| Neo4j 建库 | `cd backend && uv run python scripts/init_neo4j.py` |
| 图谱重建 | `cd backend && uv run python scripts/rebuild_graph.py --yes` |
| 后端测试 | `cd backend && uv run pytest --cov=app --basetemp=.pytest_tmp` |
| 前端类型检查 | `cd frontend && pnpm typecheck` |
| 生成 API 类型 | `cd frontend && pnpm gen:api` |
| 准确率评测 | `cd backend && uv run python scripts/evaluate.py --task all` |

---

## 9. 当前进度与下一步建议

### 9.1 当前进度（M4 收官 + M4→M5 过渡，2026.08.12 审计）

**已完成**（详见 [进度跟踪.md](../project/进度跟踪.md)）：
- ✅ 工程脚手架（前后端目录、配置、中间件、安全、ARQ worker 容器）
- ✅ API 契约 58 paths 全部实现（六模块 auth/graph/match/resume/evolution/admin，无 501 占位），真实 DB/Neo4j 端到端验证通过
- ✅ 图谱：717 条 JD 全量入图 + 163 条课程入图 + 岗位聚合（53 岗位/2409 边）+ 兜底族按技能路由治理 + 低频边/孤立节点清理
- ✅ 图算法四阶段：PageRank 技能重要性 / Louvain 技能簇 / 技能最短路径 / skill-clusters 规则后处理 + LLM 兜底（前端算法分析面板）
- ✅ LLM 抽取全量上线（Instructor 强校验 + 多 provider 重试链 + 批量 3.2x 提速）
- ✅ 匹配引擎：规则基线 + SBERT 语义增强（Spearman 0.8808）+ 领域维度 + 熟练度 + 软技能降权 + CII 通胀修正
- ✅ 新岗位发现全闭环：六状态机 + RAG 接地双路 + 辅助加分特征 + 每日自动流转 + admin 审核
- ✅ 简历解析：pypdf/PyMuPDF/OCR + PII 脱敏 + 软技能推断 + SSE 进度 + 编辑/删除/原文下载
- ✅ 演化：T+1 自动快照 + 版本 diff + 趋势 + 状态机（含 declining→stable 回迁修复）
- ✅ 数据管线：13 源爬虫（monster 停采）+ SimHash 去重 + 质量评分 + 时滞/通胀检测调优（召回率 100%/误报 0%）+ 交叉验证 + 课程评估 + 多样性报告 + 数据新鲜度检查
- ✅ 岗位治理：兜底族按技能路由、canonical_skill_name 统一入口、岗位级/平台级通胀处置、岗位可见性分级（匿名/guest 不展示 candidate）
- ✅ 黄金集：JD 100 条 + 简历 50 份 + 匹配弱监督 300 对 + 人工 JD 复核集
- ✅ 测试：后端 1168 用例（17 deselected，integration 隔离）/ 前端 28 测试文件 + Playwright E2E
- ✅ 运维：Docker Compose 5 服务 + 镜像瘦身 + webhook 告警 + 容器化采集 + 限流（100/10 req/min）

**首轮评测基线**（08.07，`scripts/evaluate.py --task all`）：JD 解析 F1=0.6112（关键词基线，未达标 0.90，M5 补 LLM 在线评测）、简历提取 F1=1.0（50 条）、人岗匹配 Spearman=0.8805 / Accuracy=0.96。

### 9.2 下一步该怎么做（按优先级）

#### 第一步：M5 打磨（08.26-09.04，当前阶段）

1. **三项准确率收尾 ≥ 90%**（AL-M5-01，关键路径）：JD 解析 F1 0.6112 → 0.90 是最大差距，补 LLM 在线评测 + 错误样例分析 + 抽取提示词迭代
2. **Bradley-Terry 权重迭代**（AL-M5-02）：匹配权重由人工标注对迭代，目标匹配准确率 ≥ 90%
3. **性能压测**（TE-M5-01 / BE-M5-01）：Locust 100 并发 P95 < 2s，重点压 panorama / match / search 端点
4. **E2E + 集成完善**（TE-M5-02 / FE-M5-02）：响应式适配 + 全流程 E2E 覆盖
5. **部署与演示物料**（DO-M5-01~04）：PPT ≥ 17 页 + 演示视频 ≤ 10 分钟 + DEPLOY.md + 源码打包

#### 第二步：初审提交（2026.09.05）

方案文档 + PPT + 视频 + 源码 + 部署说明 + 100 条黄金集 + 评测报告。

### 9.3 接手建议

1. **首次进入项目**：按 [AGENTS.md](../../AGENTS.md) 第 4.2 节顺序加载上下文（AGENTS → 项目概览 → 设计文档相关章节 → 执行计划相关模块 → 模块 README）。
2. **找待实现点**：全局搜索 `NotImplementedError` 与 `待实现` / `TODO`（M5 阶段剩余项集中在准确率调优与压测，代码骨架已无占位）。
3. **改 API 必先改契约**：[openapi/openapi.yaml](../../backend/openapi/openapi.yaml) 是单一事实源（58 paths），前端类型由 `pnpm gen:api` 生成。
4. **算法核心三道红线**：LLM 抽取 / 匹配引擎 / 演化算法 / 幻觉防控，须算法岗张恺天亲自把关，AI 仅作参考。
5. **安全代码**：密钥 / Token / 认证 / 权限相关，必须人工逐行审查，禁止 AI 自主产出。
6. **每 PR ≤ 500 行**：超出必须拆分；`main` 分支禁止直推，需 PR + CI 全绿 + ≥ 1 人 Review。
7. **Windows 环境**：git 多行提交用多个 `-m` 参数（勿用 heredoc）；pytest 固定加 `--basetemp=.pytest_tmp`（见 [postmortem](../../postmortems/001-powershell-pytest-windows-env.md)）。

---

## 10. 参考文档

| 文档 | 路径 |
|------|------|
| 文档索引 | [docs/README.md](../README.md) |
| AI 协作入口 | [AGENTS.md](../../AGENTS.md) |
| 设计文档（单一事实源） | [design/设计文档.md](./设计文档.md) |
| 执行计划 | [design/执行计划.md](./执行计划.md) |
| 项目概览 | [project/项目概览.md](../project/项目概览.md) |
| 进度跟踪 | [project/进度跟踪.md](../project/进度跟踪.md) |
| 贡献指南 | [guides/贡献指南.md](../guides/贡献指南.md) |
| 团队启动指南 | [guides/团队启动指南.md](../guides/团队启动指南.md) |
| 后端 README | [backend/README.md](../../backend/README.md) |
| 爬虫 README | [backend/data/crawlers/README.md](../../backend/data/crawlers/README.md) |
| 前端 README | [frontend/README.md](../../frontend/README.md) |
| 测试用例矩阵 | [backend/tests/test_cases.md](../../backend/tests/test_cases.md) |
| API 契约 | [backend/openapi/openapi.yaml](../../backend/openapi/openapi.yaml) |
| 项目记忆 | [project_memory.md](../../project_memory.md) |
