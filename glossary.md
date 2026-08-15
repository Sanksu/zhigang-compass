# 术语表（Glossary）

> 智岗罗盘项目术语速查。定义来源：设计文档（docs/design/设计文档.md）+ 代码实现口径。
> 交付标准：≥ 50 条术语（本表 72 条）。

## 一、系统与架构

| 术语 | 定义 |
|---|---|
| 智岗罗盘 | 多源异构驱动的岗位能力动态演化与人岗匹配系统（XH-202621），核心是构建可自我进化的"人才能力大脑" |
| 多源异构 | 融合招聘 JD、课程平台、论文/社区、国家职业分类等异质数据源，交叉验证岗位与技能信号 |
| 岗位能力图谱 | Neo4j 图数据库承载的岗位-技能-课程-证据关系网络（Position/Skill/Course/Evidence 节点） |
| 契约优先 | 铁律一：API 变更先改 `backend/openapi/openapi.yaml`（单一事实源），再写后端实现与前端类型 |
| 三层数据栈 | PostgreSQL 15 + pgvector（关系/向量/JSONB 快照）+ Neo4j 5（图 + cjk 全文索引）+ Redis 7（缓存/限流/队列） |
| 五服务架构 | Docker Compose：api / postgres / redis / neo4j / worker(ARQ) 五个容器服务 |
| 同端口托管 | FastAPI 通过 StaticFiles 同端口托管前端静态资源，中间件承担 CORS/CSP/HSTS/gzip/限流 |
| ARQ | 异步任务队列（Redis 驱动），处理简历解析、批量抽取、演化计算等耗时操作 |
| fail-fast | 生产环境强校验：SECRET_KEY 长度、Redis 密码、禁 Swagger，不满足即拒绝启动 |
| 契约测试 | 基于 openapi.yaml 的前后端接口一致性校验（前端 openapi-typescript 生成类型） |

## 二、数据采集与清洗

| 术语 | 定义 |
|---|---|
| 13 源三级分级 | 数据源按可信度分 A/B/C 三级（拉勾网 2026-08-01 移除，原 14 源；BOSS 直聘 2026-08-15 起暂停采集，恢复时间待定） |
| 代理池三梯队 | 第一梯队 Rotating 代理池 / 第二梯队 PROXY_POOL + ProxyPoolMiddleware 随机轮换 / 第三梯队直连兜底 |
| SimHash 去重 | 64-bit 语义指纹近似去重（汉明距 ≤ 3），跨平台识别同岗位 |
| 时效加权 | `weight = 1.0 (≤30天) / exp(-0.01×(days_ago-30)) (>30天)` |
| 质量评分 | 字段完整度 + 文本长度 + 核心词 + 格式规范，< 0.6 入人工复核（needs_review） |
| 重爬不重抽 | `jd_raw` 按 (source, source_id) upsert，JSONB snapshot 保留原始字段，抽取结果独立可审计 |
| 跨源置信度 | 数据源数量 × 跨源一致性 × 时效得分加权；单源数据标记 `unverified` |
| 增量调度 | 仅爬取发布时间 ≤ 7 天的新 JD，URL MD5 去重 |
| 指数退避 | 429/403 触发 30s→60s→120s→300s 退避，单日单源连续 3 次失败当日停止 |
| 黄金集 | 人工标注的评测基准集（JD 100 条 + 盲审 32 条 + 简历/匹配/时间/通胀子集） |

## 三、LLM 抽取与幻觉防控

| 术语 | 定义 |
|---|---|
| 三道防线 | 幻觉防控：① Pydantic Schema 强校验 ② 词典过滤（白名单/停用词） ③ 证据链可追溯 |
| 多 provider 重试链 | OpenAI 兼容 API 多厂商同步重试（deepseek 当前可用），超时 10s 返回 504(5003) |
| 分层 Prompt | System Prompt（角色）→ Task Prompt（提取要求）→ Few-Shot Examples（示例）三层结构 |
| 标题优先 | 岗位名以招聘标题为准，正文职责/高频技术栈不得改写标题岗位名 |
| 技能白名单 | `configs/skill_whitelist.yaml` 单一事实源（500+ 标准技能），幻觉防控第三道防线 |
| SKILL_ALIAS | 技能别名归一表（大小写不敏感），同义异构统一到白名单标准词 |
| SKILL_STOPWORDS | 业务领域词/泛词黑名单（保险/五险一金/审批等），防 LLM 幻觉技能入图 |
| 归一化覆盖率 | normalize_skill 覆盖的抽取技能比例（95% 对齐三硬指标） |
| 批量抽取 | batch_size 条数 + max_batch_chars 文本双封顶组批，每批一次 LLM 调用 |
| 错位防护 | 批量返回条数 ≠ 输入条数 → 降级逐条，防张冠李戴 |

## 四、图谱与算法

| 术语 | 定义 |
|---|---|
| Position 节点 | 图谱岗位节点（含 name/freq/status/industry 属性），经 normalize_position_name 归一化 |
| 失真兜底族 | 软件开发工程师/算法工程师等泛词族——按技能内容路由到细分族，不再作聚合目的地 |
| 技术栈前缀 | React/Vue/Golang/SLAM 等岗位名细分维度（"React前端开发工程师"独立于"前端开发工程师"） |
| LEARNABLE_VIA | 技能→课程关系边（学习路径课程匹配用） |
| REQUIRES | 岗位→技能关系边（含 weight 权重与 necessity must/nice） |
| 熟练度映射 | 精通→3 / 熟练熟悉→2 / 了解→1 / 项目中使用→2 |
| 跨域降权 | P2-C：岗位族期望技能类别白名单（_ALLOWED_SKILL_CATEGORIES），跨域技能降权 |
| PageRank | 图算法：岗位/技能重要性排名 |
| Leiden | 图算法：技能簇社区发现（2026-08-13 由 Louvain 切换） |
| 最短路径 | 图算法：岗位间技能迁移路径（转岗推荐） |
| SBERT | sentence-transformers 多语言语义模型（paraphrase-multilingual-MiniLM），语义匹配/课程推荐 |
| RAG 接地 | 检索增强生成：仅基于图谱证据生成岗位定义/回答，证据不足明确说明 |
| 技能老化指数 SAI | 同岗位近期 JD 技能分布跟随技术趋势变化的度量 |

## 五、匹配与演化

| 术语 | 定义 |
|---|---|
| 三维评分 | 人岗匹配：技能匹配度 + 经验匹配度 + 学历匹配度综合评分 |
| Bradley-Terry | 匹配权重学习模型（黄金集成对比较，Optuna 调优，Spearman 0.88） |
| 通胀修正 | CII（岗位要求通胀）修正——JD 虚高要求降权 |
| 时效衰减 | 180d→0.95 / 365d→0.85 分段衰减（基于 LinkedIn Workforce Report） |
| Z-score 门控 | 演化检测：技能频次变化统计显著性判定（+ MoM 环比辅助） |
| 技术热点观察池 | arXiv/GitHub/StackOverflow 信号池（不独立触发 candidate），admin 周报可见 |
| 媒介落差指数 MLI | 论文/课程/社区/招聘四维信号综合，MLI > 0.6 判定产业拐点 |
| 状态机 | 岗位生命周期：active（图谱常态）→ candidate → emerging → stable → declining → archived（08-15 新增 active 常态，import_jd 默认态；阈值见设计文档 §7.2.1） |
| 种子列表 | 预置 12 个新兴岗位种子（AI Agent 工程师/RAG 工程师等）引导发现 |
| graph_v{date} | 演化全量快照（APOC 导出，PostgreSQL JSONB 存储） |

## 六、学习路径与评测

| 术语 | 定义 |
|---|---|
| 先修字典 | `configs/skill_prerequisites.yaml` 人工维护的技能先修链（177 技能） |
| 学时分层 | 技能按白名单类别给基准学时（AI/算法 70h / 编程语言 55h / 基础 40h） |
| 课程质量分 | evaluate_courses 输出（字段完整度/标题相关性/时长等），≥0.65 入推荐池 |
| 中英词面豁免 | _EN_SKILL_HINTS：技能中文名 ↔ 课程英文标题关键词（跨语言 sim 虚低豁免） |
| 灰色带质量门控 | sim ∈ [0.5,0.62) 且质量分 <0.62 的课程过滤（中英短词 sim 虚高误配治理） |
| single-flight | 缓存穿透合并：同 key 并发 miss 只放行 1 个查库，其余 await 同 future |
| 盲审 | 人工盲标评测集（gold 独立于抽取器草稿），32 条合并口径 |
| annotator | 盲审标注人代号（LQ=刘琪 / ZKT=张恺天），评测预检要求非空 |
| 终审口径 | title 按招聘标题主体（去级别/括号/空格、师→工程师、保留细分词） |
| F1 目标 | 设计文档 §13.3：JD 解析 ≥ 90%（当前 LLM 盲审 0.818） |
| 学习路径合理性 | 30 案例专家评审：completeness/prerequisite/course/hours 四维 mean ≥ 0.8 |
| 预评分 vs 定稿 | 规则化 AI 预评分 → 专家复核 --re-score → 人工定稿 |

## 七、安全与合规

| 术语 | 定义 |
|---|---|
| PII 脱敏 | 简历敏感字段（姓名/电话/邮箱）脱敏存储（mask_pii） |
| RBAC | 角色权限控制（guest/user/admin，graph:read 等权限点） |
| 限流中间件 | 普通接口 100 req/min/IP/path，LLM 类 10 req/min（滑动窗口） |
| Token 黑名单 | 登出后 access token 加入 Redis 黑名单（jti 维度） |
| 弱口令门禁 | ADMIN_PASSWORD 非 admin123 强制校验 |
| robots 合规 | 采集遵循 robots.txt + 请求间隔（arxiv/github/coursera 等保守处理） |
| 错误码契约 | openapi.yaml 定义业务错误码（4000 参数/4040 资源/5000 内部/5003 超时/4290 限流等） |

---

**配套文档**：设计文档（docs/design/设计文档.md）· 执行计划（docs/design/执行计划.md）· 变更日志（CHANGELOG.md）
