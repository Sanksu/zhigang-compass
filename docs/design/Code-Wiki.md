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

> 当前项目处于 **M2 基座阶段**。多数 API 端点返回 501 占位，多数算法类的核心方法 `raise NotImplementedError`（标注 M3 由算法岗完成）。本 Wiki 既记录已实现逻辑，也标注待实现位置，便于接手者快速定位。

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

| 阶段 | 时间 | 核心交付 |
|--------|--------|---------|
| M1 方案 | 2026.07.13—07.26 | 技术方案 + 协作规范 + API 契约 |
| **M2 基座（当前）** | 2026.07.27—08.05 | 工程脚手架、Neo4j 建库、爬虫开发、清洗管线 |
| M3 核心 | 2026.08.06—08.15 | LLM 抽取、匹配引擎、图谱首版、JWT 封装 |
| M4 闭环 | 2026.08.16—08.25 | 简历匹配、管理后台、新岗位发现、学习路径 |
| M5 打磨 | 2026.08.26—09.04 | 准确率、性能压测、Docker 部署、PPT + 视频 |
| ★ 初审提交 | 2026.09.05 | 文档 + 源码 + 部署说明 |

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
│  路由：公开 / 受保护 / 管理员三类，AppShell 布局                │
└───────────────────────────┬─────────────────────────────────────┘
                            │ /api/v1/*  （Vite 代理 / StaticFiles 同端口）
┌───────────────────────────▼─────────────────────────────────────┐
│  API 层（backend/app/api/v1/）                                  │
│  FastAPI + Pydantic + 统一 APIResponse 契约                     │
│  auth / graph / match / resume / evolution 五子路由             │
└───────────────────────────┬─────────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────────┐
│  服务层（backend/app/services/）                                │
│  extraction（JD 抽取）   kg（图谱 ID 生成）                     │
│  matching（匹配引擎）    evolution（技能演化）                  │
│  discovery（新岗位发现）                                        │
└─────────┬─────────────────┬─────────────────┬───────────────────┘
          │                 │                 │
┌─────────▼───────┐ ┌───────▼───────┐ ┌───────▼───────────────────┐
│ PostgreSQL 15   │ │  Neo4j 5      │ │  Redis 7                  │
│ + pgvector      │ │  能力图谱     │ │  缓存(30s) + 限流 + ARQ   │
│ 关系/向量/JSONB │ │  全文索引(cjk)│ │  任务队列（db=1 独立）    │
└─────────────────┘ └───────────────┘ └───────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  数据管线（backend/data/crawlers/）                             │
│  Scrapy + Playwright，14 源 A/B/C 分级                          │
│  → CleaningPipeline（脱敏 + 指纹）→ PostgresPipeline（upsert）  │
└───────────────────────────┬─────────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────────┐
│  异步任务（backend/app/workers/）ARQ                            │
│  resume_parse / batch_extract / evolution_compute               │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  LLM 多 Provider 重试链（OpenAI 兼容 API）                      │
│  主 讯飞星火 → 备 DeepSeek → 三 Qwen Plus                       │
│  同步路由 10s 超时返 503；异步任务 90s 上限                     │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 数据流

```
招聘平台（14 源）
   │ Scrapy + Playwright 采集
   ▼
JobItem（统一 schema）
   │ CleaningPipeline：SHA256 指纹去重 + 脉脉脱敏 + 文本标准化
   ▼
PostgreSQL jd_raw（upsert，source+source_id 去重）
   │ LLM 抽取管线（三道幻觉防线）
   ▼
Skill / Tool / Education / Certification 实体 + REQUIRES 关系
   │ 入图谱
   ▼
Neo4j 能力图谱（Position / Skill / Evidence / Course ...）
   │ 每日 05:00 聚合预计算 + 演化检测
   ▼
匹配引擎（内容加权 + Sentence-BERT + 规则/LLM 兜底）
   │
   ▼
人岗匹配结果 / 演化信号 / 新岗位候选
```

### 2.3 关键设计原则

1. **契约优先**：API 变更先改 [openapi/openapi.yaml](../backend/openapi/openapi.yaml)，前端用 `openapi-typescript` 生成类型
2. **算法与后端解耦**：算法与后端通过共享数据层解耦读写，不通过 API 互调
3. **两层更新机制**：新证据实时入库（PostgreSQL + Evidence 节点），聚合预计算每日 05:00
4. **三道幻觉防线**：JSON Schema 强校验 → 别名归一化 → 白名单后过滤
5. **小基数保护**：频次 < 10 走 PROTECTED 态；冷启动 < 60 天走 Wilson score 兜底
6. **权重外部化**：所有权重默认值硬编码，可通过 `configs/*.json` 覆盖，文件缺失不阻断流程

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
| 数据库迁移 | Alembic | |
| 关系库 | PostgreSQL 15 + pgvector + JSONB | 一份镜像承载三能力 |
| 图数据库 | Neo4j 5 + Cypher + 全文索引（cjk 分词） | 替代 Elasticsearch |
| 缓存/队列 | Redis 7 | db=0 缓存 / db=1 ARQ |
| 异步任务 | ARQ | 并发 10，超时 300s |
| 大模型 | OpenAI 兼容 API（讯飞星火 / DeepSeek / Qwen） | Instructor + Pydantic 结构化 |
| 数据管线 | Scrapy + Playwright | SimHash/MinHash 去重 |
| 认证 | JWT RS256 双 Token + RBAC | access 30min / refresh 7d |
| 容器化 | Docker Compose（4 服务） | api / postgres / redis / neo4j |
| 测试 | PyTest + Vitest + Playwright + Locust | 测试金字塔 70/20/10 |
| Python 包管理 | uv | `pyproject.toml` + `uv.lock` |

---

## 4. 目录结构

```
zhigang-compass/
├── README.md                       # 根入口
├── AGENTS.md                       # AI 协作入口（铁律、模块导航）
├── docker-compose.yml              # 4 服务一键部署
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
│   │   ├── components/             # layout/ + ui/
│   │   ├── routes/                 # 11 个页面 + guards
│   │   ├── store/                  # auth.ts + ui.ts
│   │   ├── lib/                    # api.ts + query-client.ts + utils.ts
│   │   ├── styles/globals.css      # 设计令牌
│   │   └── types/api.d.ts          # OpenAPI 自动生成
│   ├── vite.config.ts
│   └── package.json
└── backend/                        # 后端 Python monorepo
    ├── app/
    │   ├── main.py                 # FastAPI 入口
    │   ├── api/v1/                 # 5 个路由模块
    │   ├── core/                   # config / database / middleware / security
    │   ├── models/                 # SQLAlchemy 基类
    │   ├── schemas/                # APIResponse 统一响应
    │   ├── services/               # 算法引擎（5 子模块）
    │   │   ├── extraction/         # JD 抽取管线
    │   │   ├── kg/                 # 图谱 ID 生成
    │   │   ├── matching/           # 人岗匹配
    │   │   ├── evolution/          # 技能演化
    │   │   └── discovery/          # 新岗位发现
    │   └── workers/                # ARQ 异步任务
    ├── data/crawlers/              # Scrapy 爬虫（14 源）
    ├── configs/                    # 运行时权重 JSON
    ├── openapi/openapi.yaml        # API 契约
    ├── alembic/                    # 数据库迁移
    ├── scripts/                    # init_neo4j / verify_neo4j
    ├── tests/                      # 单元/集成/E2E/评测/压测
    └── pyproject.toml
```

---

## 5. 主要模块职责

### 5.1 后端基础设施（[backend/app/core/](../backend/app/core/)）

| 文件 | 职责 |
|------|------|
| [config.py](../backend/app/core/config.py) | 配置中心。`Settings(BaseSettings)` 从 `.env` + 环境变量加载；含应用/数据库/LLM 三 provider/JWT/缓存/ARQ/前端目录配置；`is_production` 控制安全开关 |
| [database.py](../backend/app/core/database.py) | 三库连接管理：PostgreSQL（async engine + session 工厂）、Neo4j（同步 driver）、Redis（async client）；提供 `get_db` / `get_neo4j` / `get_redis` 依赖注入 |
| [middleware.py](../backend/app/core/middleware.py) | 中间件链：CORS（白名单）+ GZip（>1KB）+ SecurityHeaders（CSP/HSTS/TraceID） |
| [security.py](../backend/app/core/security.py) | JWT RS256 双 Token（access 30min / refresh 7d）+ bcrypt 密码哈希 + RBAC 四角色权限映射 |

### 5.2 API 层（[backend/app/api/v1/](../backend/app/api/v1/)）

[router 聚合](../backend/app/api/v1/__init__.py) 挂载 5 个子路由到 `/api/v1/*`，全部端点当前为 501 占位但契约已就位。

| 子路由 | 前缀 | 端点 | 职责 |
|--------|------|------|------|
| [auth.py](../backend/app/api/v1/auth.py) | `/auth` | POST `/login` `/refresh` `/register` | 认证（占位） |
| [graph.py](../backend/app/api/v1/graph.py) | `/graph` | GET `/panorama` `/skill/{id}/positions` `/search` | 图谱全景（30s Redis 缓存）/ 技能反向查询 / 全文检索 |
| [match.py](../backend/app/api/v1/match.py) | `/match` | POST `/recommend` `/compare` | 自动推荐 Top-N（ARQ）/ 人岗比对 |
| [resume.py](../backend/app/api/v1/resume.py) | `/resume` | POST `/parse` GET `/task/{id}` | 简历解析（异步任务）+ 任务轮询 |
| [evolution.py](../backend/app/api/v1/evolution.py) | `/evolution` | GET `/versions` `/diff` `/trends` | 图谱版本列表 / Diff / 技能趋势 |

统一响应契约 [schemas/common.py](../backend/app/schemas/common.py)：`APIResponse[T]`（code/msg/data/trace_id）+ `ok()` / `error()` 工具函数。

### 5.3 服务层（[backend/app/services/](../backend/app/services/)）

#### 5.3.1 extraction/ — JD 实体抽取管线

三步管线：LLM Few-Shot 抽取 → 词典后过滤 → 中文后缀清洗 + 去重。

| 文件 | 关键导出 | 状态 |
|------|---------|------|
| [schemas.py](../backend/app/services/extraction/schemas.py) | `SkillExtracted` / `ToolExtracted` / `EducationExtracted` / `CertificationExtracted` / `REQUIRESRelation` / `JDExtractionResult` | ✅ 完整 |
| [jd_extractor.py](../backend/app/services/extraction/jd_extractor.py) | `JDExtractor.extract()`（LLM + 规则兜底 + 后处理编排） | ⚠️ 规则分支可用，LLM 分支 M3 |
| [llm_provider.py](../backend/app/services/extraction/llm_provider.py) | `LLMProvider.extract_structured()` / `LLMExtractionError` | ⚠️ M3 实现 |
| [dictionary.py](../backend/app/services/extraction/dictionary.py) | `SKILL_ALIAS`（别名表）/ `SKILL_WHITELIST`（白名单）/ `normalize_skill()` | ✅ 完整 |
| [post_processor.py](../backend/app/services/extraction/post_processor.py) | `clean_skill_name()` / `dedup_skills()` / `post_process()` | ✅ 完整 |
| [prompts.py](../backend/app/services/extraction/prompts.py) | `SYSTEM_PROMPT` / `TASK_TEMPLATE` / `FEW_SHOT_EXAMPLES` | ✅ 完整（M3 启用） |

#### 5.3.2 kg/ — 图谱 ID 生成

| 文件 | 关键导出 | 状态 |
|------|---------|------|
| [id_generator.py](../backend/app/services/kg/id_generator.py) | `PREFIX_MAP`（8 类前缀）/ `next_id(tx, entity_type)` | ✅ 完整 |

格式 `{prefix}_{seq:04d}`（如 `sk_0042`），通过 Neo4j Counter 节点原子自增。

#### 5.3.3 matching/ — 人岗匹配引擎

| 文件 | 关键导出 | 状态 |
|------|---------|------|
| [schemas.py](../backend/app/services/matching/schemas.py) | `Necessity` / `SkillRequirement` / `PositionProfile` / `CandidateProfile` / `MatchRequest` / `MatchResult` | ✅ 完整 |
| [weights.py](../backend/app/services/matching/weights.py) | `load_weights()`（默认 0.6/0.2/0.2，可被 configs/match_weights.json 覆盖） | ✅ 完整 |
| [engine.py](../backend/app/services/matching/engine.py) | `MatchEngine.match()` / `RuleBasedMatcher` | ⚠️ M3 实现 |

#### 5.3.4 evolution/ — 技能演化检测

| 文件 | 关键导出 | 状态 |
|------|---------|------|
| [schemas.py](../backend/app/services/evolution/schemas.py) | `SkillEvolutionTrend`（5 趋势）/ `EvolutionSignal` / `GraphVersionMeta` | ✅ 完整 |
| [detector.py](../backend/app/services/evolution/detector.py) | `compute_zscore()` / `classify_trend()` / `EvolutionDetector` | ⚠️ 纯函数可用，类 M3 |
| [graph_version.py](../backend/app/services/evolution/graph_version.py) | `GraphVersionManager.create_snapshot()` / `diff_versions()` / `list_versions()` | ⚠️ M3 实现 |

#### 5.3.5 discovery/ — 新岗位发现

**六状态机**：`CANDIDATE → EMERGING → STABLE → DECLINING → ARCHIVED`，另有 `REJECTED` 终态。

| 文件 | 关键导出 | 状态 |
|------|---------|------|
| [schemas.py](../backend/app/services/discovery/schemas.py) | `PositionState`（6 态）/ `DiscoveryFeatures` / `ConfidenceScore` / `CandidatePosition` | ✅ 完整 |
| [confidence.py](../backend/app/services/discovery/confidence.py) | `wilson_lower()` / `compute_confidence()` | ✅ 完整 |
| [detector.py](../backend/app/services/discovery/detector.py) | `passes_gate()` / `passes_cold_start_gate()` / `DiscoveryDetector` | ⚠️ 纯函数可用，类 M3/M4 |
| [state_machine.py](../backend/app/services/discovery/state_machine.py) | `VALID_TRANSITIONS` / `PositionStateMachine.transition()` | ⚠️ 合法性校验可用，持久化 M3 |

### 5.4 异步任务（[backend/app/workers/](../backend/app/workers/)）

| 文件 | 职责 |
|------|------|
| [tasks.py](../backend/app/workers/tasks.py) | 3 个 ARQ 任务：`resume_parse` / `batch_extract` / `evolution_compute`（当前均占位） |
| [config.py](../backend/app/workers/config.py) | `ARQ_SETTINGS`：Redis db=1（与缓存隔离）、并发 10、超时 300s、重试 2 次 |

### 5.5 数据管线（[backend/data/crawlers/](../backend/data/crawlers/)）

Scrapy + Playwright + CDP，14 源招聘 A/B/C 三级分级 + 6 源非招聘数据（课程/论文/社区）。

**招聘数据源**：7 个已贯通源（BOSS / 智联 / Monster / Indeed / LinkedIn / Glassdoor / 脉脉）+ setup_boss_chrome CDP 启动器采用 **「Scrapy Spider + 独立脚本 + subprocess 隔离」** 架构，避免 Playwright(asyncio) / JobSpy(同步) 与 Scrapy Twisted 事件循环冲突。拉勾网（lagou）因阿里云 WAF 拦截 + 登录态要求，已存档待 M2 后续开发。

**非招聘数据源**（2026-07-29 新增）：6 个爬虫直接继承 `Scrapy.Spider`（非 `BaseSpider`），构造 `CourseItem` / `PaperItem` / `CommunityTrendItem`：
- 课程 3 个（icourse163/Coursera/edX）：Playwright 渲染搜索页，用于构建 `(:Skill)-[:LEARNABLE_VIA]->(:Course)` 关系
- 论文 1 个（arXiv）：官方 API + Atom XML 解析，进技术热点观察池
- 社区 2 个（GitHub Trending/Stack Overflow）：公开页 HTML 解析，进技术热点观察池
- **分层源策略**（设计文档 §7.2.2）：arXiv/GitHub/SO 信号不独立触发 candidate，仅作 candidate→emerging 阶段置信度加分（单异常 +0.10 / 双异常 +0.15，封顶 1.0）

| 文件 | 职责 |
|------|------|
| [base_spider.py](../backend/data/crawlers/base_spider.py) | `BaseSpider` 基类：关键字 × 城市遍历 + `make_item()` 统一构造 + 脉脉合规头（仅招聘源继承） |
| [items.py](../backend/data/crawlers/items.py) | `_BaseItem` 基类 + 4 子类：`JobItem`（招聘）/ `CourseItem`（课程）/ `PaperItem`（论文）/ `CommunityTrendItem`（社区趋势） |
| [pipelines.py](../backend/data/crawlers/pipelines.py) | `CleaningPipeline`（SHA256 指纹 + 脉脉脱敏 + 文本标准化，跨 Item 类型）→ `PostgresPipeline`（按 Item 类型路由，占位） |
| [middlewares.py](../backend/data/crawlers/middlewares.py) | `UARotationMiddleware`(400) + `ProxyPoolMiddleware`(410，失败剔除) + `ExponentialBackoffMiddleware`(420) |
| [scrapy_settings.py](../backend/data/crawlers/scrapy_settings.py) | 框架设置：Playwright handler + AsyncioSelectorReactor + JSONL Feed |
| [settings.py](../backend/data/crawlers/settings.py) | 业务配置：招聘平台分级 / 非招聘数据源速率限制 / 代理池（POOL_REQUIRED 含 6 个新源） / 脉脉合规 / 采集量目标 |
| [setup_boss_chrome.py](../backend/data/crawlers/setup_boss_chrome.py) | ✅ 隔离 Chrome 启动脚本（CDP 9222）：启动带真实指纹的 Chrome/Edge，用户手动登录后持久保存登录态。BOSS/Monster/Glassdoor/Maimai 共用 |
| [boss_cdp_crawler.py](../backend/data/crawlers/boss_cdp_crawler.py) | ✅ BOSS 独立采集脚本：CDP 连接 + 内部 API `/wapi/zpgeek/search/joblist.json`，输出 JSONL。参考 [eatmoreduck/boss-zhipin-scraper](https://github.com/eatmoreduck/boss-zhipin-scraper) |
| [monster_cdp_crawler.py](../backend/data/crawlers/monster_cdp_crawler.py) | ✅ Monster 独立采集脚本：CDP 连接 + XHR 拦截 `appsapi.monster.io`，绕过 DataDome。参考 [shahidirfan100/Monster-Job-Scraper](https://github.com/shahidirfan100/Monster-Job-Scraper) |
| [indeed_jobspy_crawler.py](../backend/data/crawlers/indeed_jobspy_crawler.py) | ✅ Indeed 独立采集脚本：调用 `python-jobspy` 库解析 JSON-LD 结构化数据。参考 [speedyapply/JobSpy](https://github.com/speedyapply/JobSpy) |
| [linkedin_jobspy_crawler.py](../backend/data/crawlers/linkedin_jobspy_crawler.py) | ✅ LinkedIn 独立采集脚本：调用 `python-jobspy` 库解析 JSON-LD 结构化数据。参考 [speedyapply/JobSpy](https://github.com/speedyapply/JobSpy) |
| [glassdoor_cdp_crawler.py](../backend/data/crawlers/glassdoor_cdp_crawler.py) | ✅ Glassdoor 独立采集脚本：CDP 连接 + SSR DOM 提取（JSON-LD ItemList + data-test 属性双兜底），绕过 Cloudflare |
| [maimai_cdp_crawler.py](../backend/data/crawlers/maimai_cdp_crawler.py) | ✅ 脉脉独立采集脚本：CDP 连接 + 飞书招聘页 DOM 提取（maimai.jobs.feishu.cn，无需登录态） |
| [lagou_cdp_crawler.py](../backend/data/crawlers/lagou_cdp_crawler.py) | 📦 拉勾独立采集脚本（已存档，M2 后续开发）：CDP 连接 + 内部 API `positionAjax.json` + 登录态复用，需用户先通过 WAF 滑动验证 + 登录 |
| [spiders/arxiv.py](../backend/data/crawlers/spiders/arxiv.py) | ✅ arXiv 论文爬虫：官方 API + Atom XML 解析，默认拉取 7 个 cs.* 分类，产出 `PaperItem` |
| [spiders/github.py](../backend/data/crawlers/spiders/github.py) | ✅ GitHub Trending 爬虫：公开页 HTML 解析 `article.Box-row`，提取 star/fork/language/stars_today，产出 `CommunityTrendItem` |
| [spiders/stackoverflow.py](../backend/data/crawlers/spiders/stackoverflow.py) | ✅ Stack Overflow 爬虫：标签页 HTML 解析 `div.s-post-summary`，提取 votes/views/answers/tags，产出 `CommunityTrendItem` |
| [spiders/icourse163.py](../backend/data/crawlers/spiders/icourse163.py) | ✅ 中国大学MOOC 爬虫：Playwright 渲染搜索页，解析课程卡片，产出 `CourseItem`（国内直连） |
| [spiders/coursera.py](../backend/data/crawlers/spiders/coursera.py) | ✅ Coursera 爬虫：Playwright 渲染搜索页，解析 `li.cds-grid-item` 卡片，产出 `CourseItem`（需代理） |
| [spiders/edx.py](../backend/data/crawlers/spiders/edx.py) | ✅ edX 爬虫：Playwright 渲染搜索页，解析 `div.d-card-wrapper` 卡片，产出 `CourseItem`（需代理） |
| [spiders/](../backend/data/crawlers/spiders/) | 13 个贯通 + 1 个存档：boss ✅ / zhilian ✅ / monster ✅ / lagou 📦 / indeed ✅ / glassdoor ✅ / maimai ✅ / linkedin_public ✅ / arxiv ✅ / github ✅ / stackoverflow ✅ / icourse163 ✅ / coursera ✅ / edx ✅ |

### 5.6 前端工程（[frontend/](../frontend/)）

| 模块 | 职责 |
|------|------|
| [main.tsx](../frontend/src/main.tsx) + [app/providers.tsx](../frontend/src/app/providers.tsx) | 入口 + Provider 链（QueryClientProvider → AppRouter） |
| [app/router.tsx](../frontend/src/app/router.tsx) | `createBrowserRouter`，11 页 lazy 懒加载 + AuthGuard/GuestGuard + RBAC |
| [components/layout/](../frontend/src/components/layout/) | AppShell / TopNav / Sidebar / PageHeader / PagePlaceholder / CompassMark（签名 SVG） |
| [components/ui/](../frontend/src/components/ui/) | shadcn 风格基元：Button / Card / Badge（含五状态色）/ Input |
| [routes/](../frontend/src/routes/) | 11 页面 + guards.tsx（登录/仪表盘已实现，其余占位） |
| [store/auth.ts](../frontend/src/store/auth.ts) | 认证 store（不存 token，token 走 httpOnly Cookie + 内存） |
| [store/ui.ts](../frontend/src/store/ui.ts) | UI store（sidebar 开关 + 主题切换 + localStorage 持久化） |
| [lib/api.ts](../frontend/src/lib/) | axios 实例 + 401 静默续期拦截器（refresh_token 队列） |
| [lib/query-client.ts](../frontend/src/lib/) | TanStack Query 配置（staleTime 30s，4xx 不重试） |
| [styles/globals.css](../frontend/src/styles/globals.css) | Tailwind v4 @theme 设计令牌 + 五状态色 + 深色模式 |
| [types/api.d.ts](../frontend/src/types/api.d.ts) | openapi-typescript 自动生成（30+ 端点类型） |

---

## 6. 关键类与函数说明

### 6.1 配置与安全

#### `Settings` — [core/config.py](../backend/app/core/config.py)
应用配置中心，`pydantic-settings` 驱动。
- 关键字段：`postgres_dsn` / `neo4j_uri` / `redis_url` / `llm_primary_*` / `llm_secondary_*` / `llm_tertiary_*`（三 provider）/ `jwt_*` / `panorama_cache_ttl=30` / `arq_*`
- 关键 property：`is_production`（控制 CORS/HSTS/Swagger/SECRET_KEY 守卫）、`jwt_private_key` / `jwt_public_key`（惰性读文件）

#### `create_access_token(user_id, role)` / `create_refresh_token(user_id)` — [core/security.py](../backend/app/core/security.py)
JWT RS256 签发。Access 含 `sub/role/type=access`，30min；Refresh 含 `sub/type=refresh`，7d。

#### `has_permission(role, permission)` — [core/security.py](../backend/app/core/security.py)
RBAC 校验。`admin` 持 `{"*"}` 全权；`editor`/`viewer`/`guest` 各持细粒度权限集。

### 6.2 抽取管线

#### `JDExtractor.extract(jd_text)` — [extraction/jd_extractor.py](../backend/app/services/extraction/jd_extractor.py)
JD 抽取主入口。文本 < 10 字符返回空结果；否则 LLM 抽取（M3），失败 fallback 到 `_rule_based_extract`（白名单字符串匹配）；最后 `post_process`。

#### `post_process(result)` — [extraction/post_processor.py](../backend/app/services/extraction/post_processor.py)
后处理管线：① `normalize_skill` 别名归一化 ② `clean_skill_name` 中文后缀清洗（24 个后缀）③ `dedup_skills` 去重 ④ requirements 归一化。

#### `normalize_skill(raw)` — [extraction/dictionary.py](../backend/app/services/extraction/dictionary.py)
`strip` + 查 `SKILL_ALIAS` 别名表（如 `JS→JavaScript`、`k8s→Kubernetes`）。

### 6.3 匹配引擎

#### `load_weights()` — [matching/weights.py](../backend/app/services/matching/weights.py)
加载 `(w_must, w_nice, w_exp)`，默认 `(0.6, 0.2, 0.2)`。读 `configs/match_weights.json`，文件缺失/异常回退默认值（不抛异常）。

#### `MatchEngine.match(request)` — [matching/engine.py](../backend/app/services/matching/engine.py)
匹配接口（M3 实现）。约定流程：① 倒排索引粗筛 Top-200 ② 三维评分（must/nice/exp）+ CII 通胀修正 + 时效衰减 ③ total DESC 截 Top-N。
- 缺必备技能比例惩罚：`must_penalty = 1 - (missing/total) × 0.3`
- 加分技能空集保护：`if len(nice_skills) == 0: nice_score = 1.0`

### 6.4 演化检测

#### `compute_zscore(current, mean, std)` — [evolution/detector.py](../backend/app/services/evolution/detector.py)
`(f(t) - μ) / σ`，`std=0` 返回 `0.0`。

#### `classify_trend(z_score, current_freq, protected=False)` — [evolution/detector.py](../backend/app/services/evolution/detector.py)
按阈值判定 5 趋势：
- `protected=True` 或频次 < 10 → `PROTECTED`
- `z > 2.0` → `EMERGING`；`z > 1.5` → `RISING`；`z < -1.5` → `DECLINING`；否则 `STABLE`

### 6.5 新岗位发现

#### `wilson_lower(successes, total, z=1.96)` — [discovery/confidence.py](../backend/app/services/discovery/confidence.py)
Wilson score 95% 置信区间下界，冷启动（< 60 天历史）兜底。

#### `compute_confidence(jd_count, source_count, growth_rate, arxiv_anomaly, github_anomaly)` — [discovery/confidence.py](../backend/app/services/discovery/confidence.py)
综合置信度：`w_count·norm(jd_count) + w_source·norm(source_count) + w_growth·norm(growth_rate)`（默认 0.4/0.3/0.3）。单异常 +0.10，双异常 +0.15，封顶 1.0。

#### `passes_gate(features, history_days)` / `passes_cold_start_gate(...)` — [discovery/detector.py](../backend/app/services/discovery/detector.py)
candidate 门控：正常 `z>2.0 AND source_diversity≥2 AND jd_freq_ma3≥10`（严格）或 `z>1.5 AND source_diversity≥2`（保守）；冷启动走 Wilson score > 0.3。

#### `PositionStateMachine.transition(candidate, target_state, operator, reason)` — [discovery/state_machine.py](../backend/app/services/discovery/state_machine.py)
状态转换。已实现合法性校验（查 `VALID_TRANSITIONS`，非法抛 `ValueError`），持久化 M3。

### 6.6 图谱 ID 生成

#### `next_id(tx, entity_type)` — [kg/id_generator.py](../backend/app/services/kg/id_generator.py)
通过 Neo4j Counter 节点原子自增，返回 `{prefix}_{seq:04d}`。8 类前缀：`pos/sk/ev/co/oc/ce/ed/tl`。

### 6.7 前端关键模块

#### `AppRouter` — [app/router.tsx](../frontend/src/app/router.tsx)
`createBrowserRouter` 三类路由：公开（GuestGuard）/ 受保护（AuthGuard）/ 管理员（requireRole=['admin']）。11 页 `lazy()` 懒加载。

#### `apiClient` + 401 拦截器 — [lib/api.ts](../frontend/src/lib/)
axios 实例（`withCredentials`，30s）。401 触发 `POST /auth/refresh`，期间其他 401 入队等待，成功重放，失败跳 `/login`。

#### `useAuthStore` / `useUIStore` — [store/](../frontend/src/store/)
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
        ├─ graph.py   → core.database.get_neo4j, schemas.common
        ├─ match.py   → schemas.common（M3 接 workers.tasks + services.matching）
        ├─ resume.py  → schemas.common（M3 接 workers.tasks.resume_parse）
        └─ evolution.py → schemas.common（M3 接 services.evolution）

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

services.matching:  schemas.py ← engine.py ← weights.py (+ configs/match_weights.json)
services.evolution: schemas.py ← detector.py, graph_version.py
```

### 7.2 配置文件依赖

| 代码 | 配置文件 |
|------|---------|
| [matching/weights.py](../backend/app/services/matching/weights.py) | [configs/match_weights.json](../backend/configs/match_weights.json) |
| [discovery/confidence.py](../backend/app/services/discovery/confidence.py) | [configs/discovery_weights.json](../backend/configs/discovery_weights.json) |
| [discovery/detector.py](../backend/app/services/discovery/detector.py) | [configs/emerging_seeds.yaml](../backend/configs/emerging_seeds.yaml)（种子列表） |
| LLM Provider（M3） | `configs/llm_providers.yaml`（待创建） |

### 7.3 前端依赖

```
main.tsx → app/providers.tsx (QueryClientProvider) → app/router.tsx
                                                              ├─ components/layout/app-shell.tsx
                                                              │     ├─ top-nav.tsx
                                                              │     ├─ sidebar.tsx ← nav-config.ts
                                                              │     └─ page-header.tsx / page-placeholder.tsx
                                                              ├─ routes/*.tsx (lazy) + guards.tsx
                                                              │     └─ components/ui/* + store/auth.ts + store/ui.ts
                                                              └─ lib/api.ts (axios + 401 拦截) + lib/query-client.ts

types/api.d.ts ← openapi-typescript(../contracts/openapi.yaml)
```

### 7.4 外部服务依赖

| 服务 | 用途 | 端口 |
|------|------|------|
| PostgreSQL 15 + pgvector | 关系数据 + 向量检索 + JSONB 快照 | 5432 |
| Neo4j 5 | 能力图谱 + 全文索引（cjk） | 7687 (Bolt) / 7474 (Browser) |
| Redis 7 | 缓存（db=0）+ ARQ 队列（db=1）+ 限流 | 6379 |
| LLM Provider | 讯飞星火 / DeepSeek / Qwen（OpenAI 兼容） | HTTPS |

### 7.5 Python 依赖（[pyproject.toml](../backend/pyproject.toml)）

核心：`fastapi` / `uvicorn[standard]` / `pydantic` / `pydantic-settings` / `sqlalchemy[asyncio]` / `asyncpg` / `alembic` / `pgvector` / `neo4j` / `redis` / `httpx` / `arq` / `pyjwt[crypto]` / `passlib[bcrypt]` / `instructor` / `openai` / `sentence-transformers`。
开发：`pytest` / `pytest-asyncio` / `pytest-cov` / `locust`。
爬虫（需手动添加）：`scrapy` / `scrapy-playwright` / `playwright`。

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

# 4. 一键启动 4 服务（api / postgres / redis / neo4j）
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
uv run alembic upgrade head          # 数据库迁移
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
# 需先在 pyproject.toml 添加 scrapy / scrapy-playwright / playwright / python-jobspy 并 uv sync
uv run playwright install chromium

# === 前置：CDP 浏览器（BOSS / Monster 共用，端口 9222）===
python -m crawlers.setup_boss_chrome            # 启动隔离 Chrome/Edge
# 在弹出的浏览器中：登录 zhipin.com（BOSS）+ 访问 monster.com 过 DataDome challenge
python -m crawlers.setup_boss_chrome --check    # 检查 CDP + 登录态

# === BOSS（需 CDP 浏览器保持开启）===
scrapy crawl boss -a keywords=Python -a cities=北京 -o output/boss.jsonl

# === 智联招聘（直连）===
scrapy crawl zhilian -a keywords=Python -a cities=北京 -o output/zhilian.jsonl

# === Monster（需 CDP 浏览器保持开启）===
scrapy crawl monster -a keywords=Python -a cities="New York" -o output/monster.jsonl

# === Indeed（需 Clash/V2Ray 代理）===
$env:HTTPS_PROXY="http://127.0.0.1:7890"
scrapy crawl indeed -a keywords=Python -a cities="New York" -o output/indeed.jsonl

# 采集完成后关闭 CDP 浏览器
python -m crawlers.setup_boss_chrome --stop
```

详见 [crawlers/README.md](../../backend/data/crawlers/README.md)。

### 8.5 测试与评测

```bash
# 后端测试
cd backend && uv run pytest --cov=app

# 前端测试与类型检查
cd frontend && pnpm test && pnpm typecheck

# 重新生成前端 API 类型（契约变更后）
cd frontend && pnpm gen:api

# 准确率评测（M3 启用）
cd backend && uv run python tests/evaluate/run_baseline.py
```

### 8.6 关键环境变量（[backend/.env.example](../backend/.env.example)）

```bash
APP_ENV=development                  # development | production
SECRET_KEY=<生产必须改>

# 数据库
POSTGRES_DSN=postgresql+asyncpg://zhigang:zhigang@localhost:5432/zhigang
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
REDIS_URL=redis://localhost:6379/0

# LLM 三 provider（OpenAI 兼容）
LLM_PRIMARY_BASE_URL=https://spark-api.xf-yun.com/v1
LLM_PRIMARY_API_KEY=
LLM_PRIMARY_MODEL=spark-v2
LLM_SECONDARY_BASE_URL=https://api.deepseek.com/v1
LLM_SECONDARY_API_KEY=
LLM_SECONDARY_MODEL=deepseek-chat
LLM_TERTIARY_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_TERTIARY_API_KEY=
LLM_TERTIARY_MODEL=qwen-plus

# 前端
VITE_API_TARGET=http://localhost:8000 # Vite 开发代理目标
```

### 8.7 常用命令速查

| 场景 | 命令 |
|------|------|
| 启动开发环境 | `docker compose up -d` |
| 后端热重载 | `cd backend && uv run uvicorn app.main:app --reload` |
| 前端开发 | `cd frontend && pnpm dev` |
| 数据库迁移 | `cd backend && uv run alembic upgrade head` |
| Neo4j 建库 | `cd backend && uv run python scripts/init_neo4j.py` |
| Neo4j 验证 | `cd backend && uv run python scripts/verify_neo4j.py` |
| 后端测试 | `cd backend && uv run pytest --cov=app` |
| 前端类型检查 | `cd frontend && pnpm typecheck` |
| 生成 API 类型 | `cd frontend && pnpm gen:api` |

---

## 9. 当前进度与下一步建议

### 9.1 当前进度（M2 基座，2026.07.27—08.05）

**已完成**：
- ✅ 工程脚手架（前后端目录、配置、中间件、安全、ARQ）
- ✅ API 契约骨架（5 路由 / 12 端点，全部 501 占位但契约就位）
- ✅ 服务层 Pydantic 数据模型（抽取/匹配/演化/发现四模块 schemas 完整）
- ✅ 纯函数算法逻辑（Z-score、Wilson score、置信度、词典归一化、后处理、ID 生成）
- ✅ 数据管线骨架（Scrapy + Playwright + CDP + 代理池 + 脱敏 + 13 爬虫已贯通测试产出：7 招聘源 BOSS/智联/Monster/Indeed/LinkedIn/GlassDoor/脉脉 + 6 非招聘源 arxiv/github/stackoverflow/icourse163/coursera/edx；非招聘爬虫待 M3 接入观察池与学习路径）
- ✅ 前端骨架（11 页路由 + AppShell 布局 + 设计令牌 + 登录/仪表盘初版）
- ✅ Docker Compose 4 服务编排
- ✅ Neo4j 建库脚本（约束 + 索引）
- ✅ 评测基线脚本（run_baseline.py：关键词匹配 + F1 计算 + 合理性校验，可独立运行）
- ✅ 爬虫中间件栈（UA 轮换 400 / 代理池 410 / 退避重试 420）
- ✅ 13 个爬虫测试输出 JSONL 已产出（7 招聘 + 6 非招聘）

**待实现**（标注 `NotImplementedError` 或 501 占位）：
- ⚠️ 全部 API 端点业务逻辑
- ⚠️ LLM Provider 多 provider 重试链 + Instructor 结构化抽取
- ⚠️ 匹配引擎（粗筛 + 三维评分 + 语义增强）
- ⚠️ 演化检测器 + 图谱版本快照
- ⚠️ 新岗位发现检测器 + RAG 接地
- ⚠️ 简历解析
- ⚠️ 1 个招聘爬虫已存档待后续开发（lagou 需 CDP+登录态过 WAF）；6 个非招聘爬虫已就位（arxiv/github/stackoverflow/icourse163/coursera/edx），待 M3/M4 接入观察池与学习路径
- ⚠️ 前端 9 个占位页 + ECharts 图谱接入
- ⚠️ JWT 密钥文件生成（`keys/private.pem` / `public.pem`）
- ⚠️ `configs/llm_providers.yaml` 配置
- ⚠️ **黄金集 JSONL 文件缺失**（golden_set_jd.jsonl / golden_set_resume.jsonl / golden_set_match.jsonl 均不存在），基线评测因缺少数据被跳过
- ⚠️ PostgreSQL 业务模型与 Alembic 迁移文件缺失（models/__init__.py 为空，无迁移文件）
- ⚠️ `backend/Dockerfile` 不存在（docker-compose 引用路径为空）

### 9.2 下一步该怎么做（按优先级）

#### 第一步：补齐 M2 收尾（截至 08.05）

> **审计确认的三个硬阻塞项**：黄金集、Dockerfile、JWT 密钥。这三项不完成则 M3 无法启动。

1. **[硬阻塞 🔴] 创建黄金集 JSONL**（M2 硬指标 ≥ 50 条，阻塞所有评测）
   - 按照 [annotation_guideline.md](../../backend/tests/evaluate/annotation_guideline.md) 格式
   - 首批 50 条存放于 `backend/tests/evaluate/golden_set_jd.jsonl`
   - 基线脚本 `run_baseline.py` 代码已就绪，缺数据即可运行

2. **[硬阻塞 🔴] 创建 `backend/Dockerfile`**（docker-compose 无法启动）
   - docker-compose.yml 引用 `./backend/Dockerfile` 但文件不存在
   - 需要多阶段构建：依赖安装 → 代码复制 → Uvicorn 启动

3. **[硬阻塞 🔴] 生成 JWT 密钥对**（认证模块前置依赖）
   ```bash
   mkdir -p backend/keys
   openssl genrsa -out backend/keys/private.pem 2048
   openssl rsa -in backend/keys/private.pem -pubout -out backend/keys/public.pem
   ```

4. **数据管线贯通**：实现 [pipelines.py](../backend/data/crawlers/pipelines.py) 的 `PostgresPipeline`（upsert 到 `jd_raw`），完成采集 → 清洗 → 入库闭环。

5. **SQLAlchemy 业务模型 + Alembic 首个迁移**：在 `models/` 下定义业务模型，`alembic revision --autogenerate` 生成迁移。

#### 第二步：进入 M3 核心开发（08.06—08.15）

1. **LLM 抽取上线**（算法岗张恺天）：实现 [llm_provider.py](../backend/app/services/extraction/llm_provider.py) 多 provider 重试链 + Instructor/Pydantic 结构化输出，打通 JD → 实体 → Neo4j 图谱全链路。配置 `configs/llm_providers.yaml`。

2. **图谱首版**：实现 [graph.py](../backend/app/api/v1/graph.py) 三个端点（panorama 30s 缓存 / 技能反向查询 / 全文检索），前端接入 ECharts 力导向图。

3. **匹配引擎原型**：实现 [engine.py](../backend/app/services/matching/engine.py) `RuleBasedMatcher`，目标 100 对黄金集 Spearman ≥ 0.7。

4. **JWT + API 封装**：接线 [auth.py](../backend/app/api/v1/auth.py) 登录/刷新/注册，前端登录页从 mock 切到真实 `/api/v1/auth/login`。

5. **技能归一化**：JD 抽取后技能实体归一入库，与图谱 Skill 节点对齐。

#### 第三步：M3 初性能压测（08.06—08.08）

按 [project_memory](../../) P95 预案：达标保留 / 500ms-1s 调整为 <1s / >1s 优化查询。

#### 第四步：M4 闭环 + M5 打磨（08.16—09.04）

- 简历匹配页 `/resume-match`、管理后台 `/admin`（账户/爬取/审核）、新岗位发现、学习路径
- 三项准确率 ≥ 90%、Docker 可部署、PPT + 视频定稿

### 9.3 接手建议

1. **首次进入项目**：按 [AGENTS.md](../../AGENTS.md) 第 4.2 节顺序加载上下文（AGENTS → 项目概览 → 设计文档相关章节 → 执行计划相关模块 → 模块 README）。
2. **找待实现点**：全局搜索 `NotImplementedError` 与 `待实现` / `TODO`，即当前所有骨架位置。
3. **改 API 必先改契约**：[openapi/openapi.yaml](../../backend/openapi/openapi.yaml) 是单一事实源，前端类型由 `pnpm gen:api` 生成。
4. **算法核心三道红线**：LLM 抽取 / 匹配引擎 / 演化算法 / 幻觉防控，须算法岗张恺天亲自把关，AI 仅作参考。
5. **安全代码**：密钥 / Token / 认证 / 权限相关，必须人工逐行审查，禁止 AI 自主产出。
6. **每 PR ≤ 500 行**：超出必须拆分；`main` 分支禁止直推，需 PR + CI 全绿 + ≥ 1 人 Review。

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
