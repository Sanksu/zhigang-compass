# 测试用例矩阵

> 本矩阵覆盖系统 7 大核心能力模块的端到端验证。编号规则：`TC-{模块缩写}-{序号}`。
> 模块缩写：DC（数据采集）/ KG（知识图谱）/ PD（新岗位发现）/ EV（演化）/ RM（简历匹配）/ HL（幻觉防控）/ SI（系统集成）
> 详细执行计划见 [执行计划.md](../docs/执行计划.md)。

## 模块用例索引

| 模块 | 缩写 | 用例范围 | 说明 |
|------|------|---------|------|
| 数据采集 | DC | TC-DC-01 ~ TC-DC-06 | 爬虫/清洗/去重/时滞/通胀 |
| 知识图谱 | KG | TC-KG-01 ~ TC-KG-08 | 本体/抽取/归一化/查询 |
| 新岗位发现 | PD | TC-PD-01 ~ TC-PD-07 | 状态机/特征/判定/演示 |
| 动态演化 | EV | TC-EV-01 ~ TC-EV-05 | 时间窗口/版本管理/Diff |
| 简历匹配 | RM | TC-RM-01 ~ TC-RM-07 | 文件解析/PII/抽取/匹配/差距/路径 |
| 幻觉防控 | HL | TC-HL-01 ~ TC-HL-05 | 三道防线/证据/RAG |
| 系统集成 | SI | TC-SI-01 ~ TC-SI-05 | 部署/安全/性能/降级 |

---

## DC 数据采集（TC-DC-01 ~ TC-DC-06）

> 王鹏羽负责，M2 末补充详细步骤。覆盖爬虫分级、清洗管线、SimHash 去重、SAI 时滞检测、通胀检测。

| 编号 | 场景 | 前置条件 | 步骤 | 预期结果 | 优先级 |
|------|------|---------|------|---------|--------|
| TC-DC-01 | A 级爬虫每日 P0 达标 | BOSS/智联/Monster 爬虫在线 | 检查 Airflow 仪表盘 24h 采集量 | 国内 ≥ 60 条 + 国际 ≥ 40 条，总 ≥ 100 条 | P0 |
| TC-DC-02 | SimHash 去重准确性 | 黄金集 100 条标注 JD | 运行去重管线，对比标注 | 准确率 ≥ 95% | P0 |
| TC-DC-03 | SAI 内容时滞检测 | 90 天历史 JD 已入库 | 注入 1 条技能老化的 JD | 标记 content_stale，降权 ×0.5 | P1 |
| TC-DC-04 | 抄袭时滞检测 | 旧 JD 已入库 | 注入 1 条抄袭改日期 JD | 标记为抄袭改日期，降权 ×0.4 | P1 |
| TC-DC-05 | 技能通胀检测 | 黄金集含通胀 JD | 运行通胀检测管线 | inflation_score 分级正确，高通胀降权 ×0.4 | P1 |
| TC-DC-06 | 跨平台交叉验证 | 多源 JD 已入库 | 运行 cross_validate.py | 输出 validation_report，跨源置信度 ≥ 0.6 入图谱 | P0 |

## KG 知识图谱（TC-KG-01 ~ TC-KG-08）

> 王鹏羽负责，M3 初补充详细步骤。覆盖本体约束、实体抽取、技能归一化、图谱查询、反向查询。

| 编号 | 场景 | 前置条件 | 步骤 | 预期结果 | 优先级 |
|------|------|---------|------|---------|--------|
| TC-KG-01 | 本体约束完整性 | Neo4j 已建库 | 验证 8 类实体 UNIQUE 约束 + 4 加速索引 | 所有约束生效，重复插入报错 | P0 |
| TC-KG-02 | LLM 实体抽取准确性 | 黄金集 100 条 JD | 运行抽取管线，字段级对比 | 准确率 ≥ 90%，F1 ≥ 0.88 | P0 |
| TC-KG-03 | 技能归一化 | Sentence-BERT 模型就绪 | 输入 10 组相似技能名 | 相似度 ≥ 0.85 自动建 SIMILAR_TO 关系 | P0 |
| TC-KG-04 | panorama 端点 30s TTL 缓存 | Neo4j 有数据 | 连续 2 次请求 panorama | 第 2 次响应 < 100ms（缓存命中） | P0 |
| TC-KG-05 | 技能反向查询 | 技能节点有 REQUIRES 关系 | GET /skill/{id}/positions | 返回岗位列表 + necessity + weight + level | P0 |
| TC-KG-06 | 视图切换（4 种） | Neo4j 有数据 | 请求 panorama/techStack/level/positionCenter | 各视图返回正确子图 | P1 |
| TC-KG-07 | 全文检索 | Neo4j 全文索引就绪 | GET /search?q=Java | 返回匹配节点，cjk 分词正确 | P0 |
| TC-KG-08 | 图谱版本回溯 | graph_versions 表有快照 | GET /evolution/diff?from=v1&to=v2 | 返回 added/removed/modified 列表 | P1 |

## PD 新岗位发现（TC-PD-01 ~ TC-PD-07）

> 王鹏羽负责，M3 末补充详细步骤。覆盖状态机、特征定义、JD 单道触发、置信度加分、双案例演示。

| 编号 | 场景 | 前置条件 | 步骤 | 预期结果 | 优先级 |
|------|------|---------|------|---------|--------|
| TC-PD-01 | 岗位状态机流转 | M3 起跑信号检测 | 验证 candidate→emerging→stable→declining→archived 流转 | 各状态转换条件正确触发 | P0 |
| TC-PD-02 | JD z_score 单道触发 candidate | 90 天 JD 数据已回爬 | 注入 1 条技能频次 z_score > 2.0 的 JD | 进入 candidate 池，不依赖 arxiv/github | P0 |
| TC-PD-03 | arxiv/github 不独立触发 candidate | 技术热点观察池有数据 | 验证 arxiv δ>2σ 信号 | 仅进观察池，不进 candidate 池 | P0 |
| TC-PD-04 | 观察池升级路径 | 观察池有技术热点 | 模拟 JD 偶发出现该技能 | 自动提升为 candidate 候选 | P1 |
| TC-PD-05 | candidate→emerging 置信度加分 | candidate 池有数据 | 触发 arxiv_anomaly 或 github_anomaly | 置信度 +0.10（单异常）或 +0.15（双异常） | P0 |
| TC-PD-06 | 双案例演示-预置种子 | M3 人工触发 | 走全流程产出 emerging 岗位 | 岗位定义由 LLM 生成，附 evidence_id | P0 |
| TC-PD-07 | 双案例演示-真实自动发现 | M4 自动判定 | 等待真实自动产出 | 8/28 前产出 1-3 个 emerging；若未达则交付 candidate + 声明 | P1 |

## EV 动态演化（TC-EV-01 ~ TC-EV-05）

> 王鹏羽负责，M3 末补充详细步骤。覆盖 90 天窗口、版本快照、Diff 对比、趋势查询。

| 编号 | 场景 | 前置条件 | 步骤 | 预期结果 | 优先级 |
|------|------|---------|------|---------|--------|
| TC-EV-01 | 90 天滑动窗口 Z-score | 90 天 JD 数据 | 计算技能 z_score | z_score > 2.0 → emerging 信号 | P0 |
| TC-EV-02 | Wilson score 冷启动 | 数据不足 90 天 | 计算冷启动技能 | wilson_lower > 0.3 → candidate | P1 |
| TC-EV-03 | T+1 版本发布 | Airflow 05:00 执行 | 验证 graph_versions 表 | 新版本快照生成，≤ 30s 内可见 | P0 |
| TC-EV-04 | 版本 Diff 对比 | 2 个版本快照 | GET /evolution/diff | 返回 added/removed/modified 正确 | P1 |
| TC-EV-05 | 趋势查询 | 技能有历史频次 | GET /evolution/trends?skill=Java | 返回频次 + 增长率序列 | P1 |

## RM 简历匹配（TC-RM-01 ~ TC-RM-07）

> 王鹏羽负责，M3 末补充详细步骤。覆盖文件解析、PII 脱敏、LLM 抽取、匹配计算、差距分析、学习路径。

| 编号 | 场景 | 前置条件 | 步骤 | 预期结果 | 优先级 |
|------|------|---------|------|---------|--------|
| TC-RM-01 | 简历解析（4 种文件类型） | 黄金集 50 份 | 解析 PDF/Word/图片/扫描件 | 准确率 ≥ 90% | P0 |
| TC-RM-02 | PII 脱敏 | 简历含手机/身份证/邮箱 | 运行 mask_pii() | 正则替换正确，占位符保留语义 | P0 |
| TC-RM-03 | LLM 简历抽取 | 脱敏后文本 | LLM 抽取结构化 JSON | Pydantic Schema 校验通过 | P0 |
| TC-RM-04 | 自动推荐 Top-N | 简历已解析 | POST /match/recommend | Top-10 推荐，Spearman ≥ 0.85 | P0 |
| TC-RM-05 | 人岗比对 | 简历 + 岗位已选 | POST /match/compare | 五维雷达图 + 差距分析 + 学习路径 | P0 |
| TC-RM-06 | CII 通胀修正 | 岗位 must_skills > 7 | 运行 apply_cii_correction | 边缘必备项降级为 nice | P1 |
| TC-RM-07 | 证据追溯 | 匹配结果已生成 | 点击 evidence_id | 跳转至原始 JD/论文 | P0 |

## HL 幻觉防控（TC-HL-01 ~ TC-HL-05）

> 王鹏羽负责，M3 末补充详细步骤。覆盖三道防线、证据引用、RAG 接地。

| 编号 | 场景 | 前置条件 | 步骤 | 预期结果 | 优先级 |
|------|------|---------|------|---------|--------|
| TC-HL-01 | JSON Schema 强校验 | LLM 抽取结果 | 注入字段缺失的 JSON | 自动重试 1 次，失败降级规则提取 | P0 |
| TC-HL-02 | 跨源交叉验证 | 实体仅 1 源出现 | 验证实体状态 | 标记 unverified，不入生产图谱 | P0 |
| TC-HL-03 | 白名单后过滤 | 实体不在 500+ 白名单 | 运行词典后过滤 | 对齐 O*NET/Wikidata，未对齐走审核 | P0 |
| TC-HL-04 | evidence_id 覆盖率 | 匹配结果已生成 | 自动校验 | 100% 实体有证据引用 | P0 |
| TC-HL-05 | RAG 接地 | candidate 已触发 | RAG 检索权威岗位库 | 匹配后生成岗位定义草案 | P1 |

## SI 系统集成（TC-SI-01 ~ TC-SI-05）

> 王鹏羽负责，M2 末补充详细步骤。覆盖部署、安全、性能压测、多 provider fallback、降级策略。

| 编号 | 场景 | 前置条件 | 步骤 | 预期结果 | 优先级 |
|------|------|---------|------|---------|--------|
| TC-SI-01 | 4 服务一键部署 | docker-compose.yml 就绪 | docker compose up -d | 4 服务（api/postgres/redis/neo4j）健康检查通过 | P0 |
| TC-SI-02 | FastAPI 托管前端 | 前端已构建 | 访问 http://localhost:8000 | 返回前端页面，API 路由 /api/v1/* 优先匹配 | P0 |
| TC-SI-03 | 安全中间件 | FastAPI 已启动 | 验证响应头 | CORS/CSP/HSTS/gzip 中间件生效 | P0 |
| TC-SI-04 | 基准压测（M3 初） | M2 末数据已入库 | Locust 100 并发压测 4 类端点 | P95 按分档预案处理，报告写入 docs/perf_baseline_{date}.md | P0 |
| TC-SI-05 | 多 provider 同步重试链 | 3 个 provider 配置就绪 | 模拟主 API 超时 | 同步路由 10s 返回 503；异步任务切备 provider | P0 |

---

## 用例统计

| 模块 | P0 | P1 | 小计 |
|------|----|----|------|
| DC | 3 | 3 | 6 |
| KG | 6 | 2 | 8 |
| PD | 5 | 2 | 7 |
| EV | 2 | 3 | 5 |
| RM | 5 | 2 | 7 |
| HL | 4 | 1 | 5 |
| SI | 5 | 0 | 5 |
| **合计** | **30** | **13** | **43** |

---

## 新增用例（grilling 决策补充，TC-GRILL-01 ~ TC-GRILL-06）

> 2026-07-28 grilling 决策产生的 6 类新用例，覆盖压测验证、多 provider fallback、双案例演示。

| 编号 | 场景 | 前置条件 | 步骤 | 预期结果 | 优先级 |
|------|------|---------|------|---------|--------|
| TC-GRILL-01 | panorama 30s TTL 缓存一致性 | Neo4j 有数据 | 1. 首次请求 panorama 回源 Neo4j 2. 30s 内第 2 次请求命中缓存 | 第 2 次响应 < 100ms；30s 后缓存过期回源 | P0 |
| TC-GRILL-02 | T+1 30s 一致性窗口 | 05:00 版本发布时刻 | 04:59:59 与 05:00:30 分别请求 | 05:00:30 后请求返回新版本数据 | P1 |
| TC-GRILL-03 | 多 provider 同步路由超时 | 主 API 故障 | 同步路由请求 LLM | 10s 超时返回 503，不重试 | P0 |
| TC-GRILL-04 | 多 provider 异步任务重试 | 主 API 故障 | 异步任务请求 LLM | 主 30s → 备 30s → 三 30s，90s 上限 | P0 |
| TC-GRILL-05 | 分层源-JD 单道触发 | JD z_score > 2.0 | 验证 candidate 触发 | JD 信号触发，arxiv/github 不独立触发 | P0 |
| TC-GRILL-06 | 双案例演示交付 | M5 第 3 天 | 验证 17.3 交付 | 预置案例 emerging + 真实案例（emerging 或 candidate + 声明） | P0 |

## 最终统计（含 grilling 补充）

| 模块 | P0 | P1 | 小计 |
|------|----|----|------|
| DC | 3 | 3 | 6 |
| KG | 6 | 2 | 8 |
| PD | 5 | 2 | 7 |
| EV | 2 | 3 | 5 |
| RM | 5 | 2 | 7 |
| HL | 4 | 1 | 5 |
| SI | 5 | 0 | 5 |
| GRILL | 5 | 1 | 6 |
| **合计** | **35** | **14** | **49** |

---

## 待补充

- 各用例的详细测试数据准备（黄金集 / mock 数据）
- 各用例的自动化脚本路径（`tests/unit/`、`tests/integration/`、`tests/e2e/`）
- 各用例的责任人分配（王鹏羽主导，各模块负责人协助）
- 各用例的执行时间点（M2 末 / M3 末 / M4 末 / M5 末）
