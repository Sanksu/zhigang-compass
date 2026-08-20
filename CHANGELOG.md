# 变更日志（CHANGELOG）

> 智岗罗盘——多源异构驱动的岗位能力动态演化与人岗匹配系统（XH-202621）
> 项目周期：2026.07.13 — 2026.09.05。本文件按里程碑汇总主要变更（git 历史 620+ commits / 190+ merged PR）。

## M5（2026-08-26 — 09-04）：交付冲刺

### 2026-08-21
- **ETL 队列接入配置中心 + 调度迁移容器内 ARQ cron**（#348）：前端配置中心新增「ETL 队列」分区（`/admin/settings/etl`，批次上限/默认批次 + 每日调度时间）；`runtime_config` 新增 `etl_batch_cap`/`etl_structure_load_default`/`etl_validate_temporal_default`/`etl_run_hour`/`etl_run_minute`（openapi 契约 + 前端类型重生成）；`etl.py` 批次上限与阶段默认批次改读配置，新增 `run_etl_pipeline_scheduled` 容器内 cron 入口（当日幂等 Redis 锁，与 `etl_daily` 同语义），`settings.py` 注册 ETL cron（时间取配置，重启生效），替代外部 Windows 计划任务调度；后端 20 单测 + 前端 170 测试/lint/typecheck 通过

### 2026-08-20
- **必备技能单源兜底 + 匹配候选边缘过滤**（#338，已合入）：`aggregation._is_must` 对 `jd_count≤2` 单源/少源岗位直接继承抽取层 must 标注（此前 hit<3 样本保护使单源岗位必备技能全判 nice，前端多数岗位无必备技能）；`matching/loaders` 匹配候选剔除 `freq<3 / status=legacy` 边缘岗位（GSBOA/Clay/TeamCenter基础设施管理员 等单源噪声不再进推荐）；候选 116→43；在线图重聚合后含必备技能岗位 38→82
- **学习路径双轨制 + 差距分析数据升级**（#340，已合入）：新增学习时间轴（`learning-timeline.tsx`，先修拓扑分层 → 阶段/任务卡）+ 宏观 DAG 视图（`graph-2d`，lr 分层 + 状态配色 + 有向箭头）+ 导学面板升级（`node-detail-panel`，为什么学/如何开始）+ 差距分析双轨对齐条/证据溯源/高 ROI 核心突破点打标（`resume-match-page`）；前端 155 tests 通过
- **匹配/学习路径契约字段 + 后端测算回填**（#341，已合入）：openapi `GapSkill`/`LearningPathItem` 增加可选 `demand/trend/roi/high_roi/evidence/status` + 新增 `MatchEvidenceItem`（契约优先）；后端 `gap.py` 回填 demand=source_count/20 归一化、trend=岗位扩散+跨源扩散连续信号（替代失效的 EVOLVED_FROM 演化信号，技能维度无演化边）、roi=(demand×(trend+1))/cost、evidence=JD 要求/简历现状、high_roi=真缺口 ROI Top3；`generator.py` 回填 status=doing；learning_path+matching 183 passed

### 2026-08-18
- **AL-M5-06 学习路径先修字典键名校验收官**：`app/services/learning_path/prerequisites.py` 先修链/学时查找先经 `canonical_skill_name` 归一（图谱技能名 ↔ 字典键对齐，覆盖大小写/别名/NLP↔自然语言处理 等）；并发补 40+ 高频通用技能先修链（`configs/skill_prerequisites.yaml` 08-18 五/六轮）；30 案例学习路径评测 **prerequisite 0.77→0.92、hours 0.836→0.916、合理性 80%→90%，course 0.879 保持 ≥0.85**；`tests/learning_path` **51 passed**、ruff 干净

### 2026-08-15
- **性能压测达标**（#194）：panorama P95 430ms / search P95 390ms（100 并发，<2s 目标）
  - 修复 panorama 路由装饰器错位（生产 bug，scope/focus 误暴露为必填 Query）
  - Neo4j 连接池 30→100；search 60s Redis 缓存；panorama single-flight 缓存穿透合并
  - 压测报告 `docs/perf_baseline_20260815.md`
- **学习路径专家定稿**（#187-193）：合理性 26.7%→96.7%（30 案例专家复核）
  - 碎片技能治理（英文碎片归一 6 组 + 业务词停用 4 个）
  - 课程池补采 180 门（edx 46 + icourse163 134）+ 中英词面豁免 21 组 + 门控兜底修复 + 灰色带质量门控
  - 先修字典补录四轮 64 技能（113→177）；专家定稿 `learning_path_eval_30_final.json`
- **岗位白名单补录**（#186）：鸿蒙开发工程师（变体合并）/ STEM讲师 / 统计师 / IC验证工程师 + P5 跨域白名单

### 2026-08-15（晚间批次 #195~#215）
- **M5 物料定稿**（#195）：CHANGELOG / glossary（70 术语）/ DEPLOY 定稿 + PPT 大纲 20 页（`docs/m5/PPT大纲.md`）+ 源码打包脚本（`scripts/package_release.sh`，1.9M 验证）
- **性能与加固**（#196~#198/#201）：中危批次 5（JWT audience 强制校验 / ILIKE 通配符转义 / PII 脱敏补强 / 缓存修复 / 清理备份）；爬虫超时保护死代码修复（H1，gather 包进 wait_for）+ aggregate_positions to_thread 化；graph API 课程语义门控接入（与 learning-path compare 口径一致）；课程技能缓存空结果重查 + ETL 计划任务日志名固定
- **课程池治理**（#199）：coursera xdpModal 重复课程归并（28 组/27 重复，LEARNABLE_VIA 边转移后 DETACH DELETE）
- **仓库整理**（#200/#202）：`script/` → `scripts/` 统一；postmortems 归位 `docs/`、冷启动指南入 `guides/`、删除空壳 uploads/ 与误生成 package-lock.json
- **部署修复**（#203）：compose 补 `ARQ_REDIS_URL` + uploads 共享卷（简历解析/匹配链路恢复）
- **演化看板体验与性能**（#204~#206）：岗位演化 / 技能频次默认展示 Top-8（新增 `GET /evolution/positions`、`/evolution/skills`）+ SPA 路由刷新 404 修复；演化列表 O(N×E) → 快照索引化（positions 3.8s→1.28s）
- **stable 状态机 §7.2.1 四维全对齐**（#207/#210/#212/#214/#215）：jd_count ≥ 5 显式门槛（替代 confidence≥0.8）+ skill_novelty 补齐（Skill.first_seen 平均图谱年龄归一化，5941/5941 覆盖）+ 参考周期自适应图谱生命周期（冷启动修正）+ 阈值 0.3→0.2（08-15 需求调整）；设计文档 §7.2.1 同步至实现口径（#211 修复 #210 引入的 tasks.py 重复函数体 CI 回归）
- **JD prompt 第 5 轮迭代**（#208）：别名归一 7 组 + 停用词 22 + 规则 6「同一技能单位置」硬约束 + 示例 14 → skills F1 0.818→0.83
- **学习路径 100% 定稿**（#209）：lp_10 可靠性课程补采 165 门（4 门入库，course 0.58→0.72 / mean 0.78→0.82）+ lp_17 先修补录 4 技能（mean 0.74→0.93）
- **暂停 boss 源采集**（#213，08-15 用户要求）：spider 代码保留，恢复时移回 `domestic_platforms` 列表

### 2026-08-16~08-17（冲刺批次 #236~#289）
- **JD 解析达标收官（AL-M5-01）**（#276/#281/#283~#285/#289）：盲审集扩至 51 条（逻辑标注 19 条）+ prompt r6/r6.1/r6.2 深度迭代（长清单完整性 + 等列举校准 + gold 口径修正）+ few-shot 扩示例 15-16 + 词面守卫与评测确定性补漏 → **51 条 F1 0.884→0.950 稳定达标（≥0.90）**
- **Bradley-Terry 权重迭代闭环（AL-M5-02）**（#266/#269）：BT 匹配黄金集 v2（384 对，补 nice/exp 维度标注 + 年限不足负例，`build_match_golden_v2.py`）+ 权重确认启用 `w_must=0.493/w_nice=0.108/w_exp=0.398` + `sim_threshold=0.898`（v2 Spearman 0.7642 / Acc 0.8281；exp 维度脱离 v1 退化）
- **课程语义 ≥0.85 收尾**（#273）：token 词面匹配 + 兜底阈值对齐 + 补采 4 门课
- **岗位治理**（#260/#264/#271）：重复岗位别名表合并 + 岗位名碎片归位（团队名/技术栈/产品名映射）+ Stage B 晚复核归位与 AI 客户类拦截
- **前后端清理重构**（#236~#243/#278~#280/#282/#288）：后端服务/去重/依赖/状态核对重构 + 死代码清除 + 爬虫子进程样板收敛；前端共享工具收敛（errMsg/isDark/COLOR_BY_STATUS）+ 演化看板孪生组件参数化（~350→200 行）
- **图谱与管理后台**（#274/#275）：图谱岗位展示优化（重叠修复/展开描边/力导向校准）+ 管理后台侧栏层级分组
- **CSP 修复**（#277）：放行 style-src/font-src（ECharts tooltip 内联样式与 data: 字体此前被 default-src 拦截）
- **文档**（#262/#267/#270/#286）：重复岗位治理记录 + 人工决策记录（S-01~S-10）+ 排期清单闭环核查 + JD ≥0.90 达标与算法核心 PR 补看确认记录

### 2026-08-18（批次 #290~#304）
- **学习路径评测样本扩充 30→50**（#290）：配对固化 + AI 预审 + 定稿
- **真实 JD 候选池与标注数据包**（#291）：智联 158 条（accepted 135 / review_required 23，12 类岗位）+ 110+25 分层抽样标注包（Gold 全空，不造假，供人工标注）
- **架构重构批次**（#292~#298）：岗位归一化收敛（`snapshot.normalized_position` 持久化 + 统一读取 + 可审计回填）、ARQ worker 模块化（settings/utils/diagnosis/matching/etl/crawl/discovery + 入口统一 `app.workers.settings.WorkerSettings`）、诊断报告异步化（POST 创建 + ARQ 生成 + 前端轮询）、岗位画像 Redis 版本化共享缓存（跨进程单飞 + 预热）、admin 路由域拆分、前端审核页四 Tab 拆分、图谱查询仓储拆分（services/graph）、图谱热路径 Neo4j AsyncDriver 迁移 + 生命周期清理
- **性能治理**（#300~#302）：100 并发压测对比发现并修复三层尾部问题（panorama 映射出事件循环 / TTL 30s·60s→300s + 管理端写路径即时失效 / search 补 single-flight + panorama 预序列化响应）→ **P99.9 3700→390ms（panorama）、340ms（search），P50/P95/P99 全面超越 08-15 基线**
- **LinkedIn 采集聚焦治理**（#303）：技术岗标题关键词白名单（中文子串 + 英文词边界匹配），实测 91→13 条，聚焦技术岗口径
- **运维收尾**（#300/#304）：容器挂载 reports 目录（freshness 报告持久化，修复容器重建丢失）；移除 `app.workers.tasks.WorkerSettings` 兼容导出
## M4（2026-08-16 — 08-25）：打磨冲刺

### 2026-08-14
- **黄金集盲审终审定稿**（#182）：32 条人工 gold 正式基线 F1 0.7563（LQ+ZKT 双标注人）
- **JD prompt 迭代**（#183-185）：title_hint 链路修复（评测对齐生产标题输入）+ few-shot 迭代 → skills F1 ~0.818、title_norm 0.9688
  - SKILL_STOPWORDS 停用 28 个 LLM 高频误抽词（防图谱污染）
- **学习路径改进**（#180-181）：评审重跑基线 13.3%、先修补录 91→96
- **全项目代码审查**（#148-151 等）：12 高危修复（错误码契约 47 处 / admin 后门 / 上传 DoS / 限流键 / 进程树 / 中危加固）
- **数据链路**：ETL 阶段隔离（#175）、爬虫上限统一（#162/166/176）、trend 源恢复（#174）、AWS 课程质量修复（#153/177/178/179）

### 2026-08-13
- 盲审 round2 扩充 20 条（#139-141）：终审口径确定（无空格/无级别/师→工程师/保留细化词）
- JD prompt 标题优先迭代（#141）：精简指令 + few-shot（长指令有害教训）
- 部署演练闭环：5 服务 12 项冒烟全通
- 学习路径语义门控 + 学时分层（#140）

### 2026-08-12
- 评测体系打通（#106-118）：JD 关键词基线 F1 0.60→0.76、LLM 盲审归档链修复（0.6749→0.7800）、industry 抽取补强（非空率 74%）、RAG 检索质量 recall@1=1.000
- 归一化覆盖率 95% 指标对齐（#112）

## M3（2026-08-06 — 08-15）：功能完善

- 图算法四阶段闭环（#73-101）：PageRank / Louvain 技能簇 / 最短路径 / skill-clusters LLM 兜底
- 岗位治理：兜底族按技能路由、通胀处置与聚合降权、归一化统一入口
- OSTA 职业库全量补齐（1676 详情 + hrss 定义 1677）
- 黄金集 PII 脱敏 + 标注污染修复（#161/168）
- 匹配语义增强（SBERT + Optuna 调优，Spearman 0.88）
- 多平台交叉验证、时滞/通胀调优（召回率 100%/误报 0%）

## M2（2026-07-27 — 08-05）：核心实现

- 采集 13 源 A/B/C 三级（国内直连 + 国际代理池）
- 图谱构建：Neo4j 5 建库 + cjk 全文索引（替代 ES）
- LLM 抽取管线：prompts 分层设计（System + Task + Few-Shot）、Pydantic Schema 强校验
- 匹配引擎：RuleBasedMatcher 规则基线 + 三维评分 + 时效衰减
- 演化检测：Z-score + MoM + 小基数保护
- 学习路径：先修字典 + 课程匹配 + 学时估算

## M1（2026-07-13 — 07-26）：方案与脚手架

- 技术方案 18 节全覆盖（设计文档）
- 协作规范：AGENTS.md / 贡献指南 / 分支策略 / PR 流程
- 项目脚手架：前后端工程化（React 19 + FastAPI + Docker Compose 5 服务）

---

## 版本约定

- 分支：`<type>/<模块前缀>-<简述>`；commit：`<type>(<scope>): <description>`
- 主线策略：feature 从 develop 切出 → PR + CI 全绿 + ≥1 Review → squash merge
- 详细 PR 记录见 GitHub（190+ merged PR，与头部统计同源）
