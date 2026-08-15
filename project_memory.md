# Project Memory — 智岗罗盘项目记忆

> 性质：项目级规则与踩坑记录（AGENTS.md §7 指定落点）。
> 变更需经用户确认后写入，不直接修改 AGENTS.md。

## 规则与约定

- **测试约定**：pytest-asyncio 未配置 auto 模式，async 测试须显式 `asyncio.run(...)` 包裹（遵循项目现有测试模式），不要依赖 pytest-asyncio 自动运行。
- **integration marker**：`-m integration` 标记的测试默认不参与全量运行（`-m not integration` 排除），需要真实外部服务。
- **ARQ 任务参数名**：超时参数为 `job_timeout`（非 `task_timeout`，坑 22）。
- **提交信息规范**：使用中文编写 commit message（`.trae/rules/git-commit-message.md`）。

## 坑记录

- **pytest-asyncio**：`pyproject.toml` 未配置 `asyncio_mode`，async def 测试会报 "not natively supported"，必须显式 `asyncio.run`。
- **position_freq_windows 同名合并**：graph_versions 快照中同名归一化岗位可能对应多个 pos_id，重建窗口序列时应**逐窗口求和**（该岗位当期被引用的总边数），而非取最长序列。
- **单期序列判定**：`evaluate_auto_transition` 对单期窗口波动为 0 会判定 STABLE，因此冷启动闸门（快照 < 2 期直接跳过）必须保留在任务层 `discovery_auto_transition`，判定层不做防御。

## 自动状态流转（AL-M4-05，设计文档 §7.2.1/§7.2.4）

- 数据源：`graph_versions` 快照序列（岗位频次 = 岗位作为边 source 的计数），与 trend_service 同源。
- 任务链：`discovery_daily`（候选池 + RAG 接地）→ `discovery_auto_transition`（emerging/stable/declining 自动流转）。
- 调度入口：`scripts/cron/discovery_daily.py` 每日 05:30 入队两个 ARQ 任务（Linux `crontab.example` / Windows `scheduled_tasks.ps1`）。
- emerging→stable 阈值：confidence ≥ 0.8 且连续 2 窗口波动 < 25% 且源 ≥ 2（§7.2.4）。
- 定义草案：`_generate_definition` 走 LLM 中文凝练（instructor 强校验），失败静默回退权威库原文/种子描述，不阻塞接地。

## 图谱质量观察（2026-08-09）

- **算法工程师过度聚合（freq=486，图谱最大低频聚合源）**：算法通用族（`_POSITION_KEYWORDS` 兜底分支）把大量无方向词的"算法工程师"JD 全部收编，技能边 414 条混聚至少 3 个方向（LLM/视觉机器人/后端基建），节点语义失真为"泛技术岗"。细分族（大模型/机器视觉/自动驾驶等）仅对方向词显式出现在岗位名时命中。根因是通用族关键词覆盖几乎全部方向词，属归一化设计缺陷，非证据错误。
- 影响：技能反向查询/先修链/课程推荐基于混杂集合，质量被稀释；低频方向技能（source_count=1）也被带入。
- 处理方向（已实施 2026-08-09）：按 JD 技能内容聚类归位（discovery 已有方向标注能力），而非继续扩关键词表；直接拆分会导致大模型算法(12源)/机器视觉(8源)等小节点统计意义弱。
- 算法工程师纳入技能路由（2026-08-09 追加）：实测 657 条算法工程师 JD → 497 可路由（大模型 176/机器视觉 94/机器人 21 等），160 无路可归（140 无技能），仅纯通用算法技能（机器学习/深度学习/pytorch）的 108 条合法保留本族。

### 语义失真聚合节点全量扫描（2026-08-09，同根因：兜底关键词聚合）

全部 59 岗位逐一核对技能集合后的失真清单（按影响排序）：

| 岗位 | freq | 技能数 | 失真类型 |
|---|---|---|---|
| 算法工程师 | 486 | 414 | LLM+视觉机器人+后端基建混聚（已记录） |
| 软件开发工程师 | 319 | 213 | HR系统(ServiceNow/HRIS)+移动(Android/Kotlin)+AI(强化学习/Agentic)+通用后端 混聚；英文 `software *` / `member of technical staff` 全归此节点 |
| Python开发工程师 | 117 | 103 | AI(检索增强生成/AutoGen/自动驾驶)+爬虫+前后端混入；`("python",)` 兜底 |
| 架构师 | 35 | 84 | 软件架构(微服务/Spring)+数据架构(数据管道/治理)+AI架构(LLM/向量库) 混聚；`("架构",)` 兜底 |
| 科学家 | 32 | 39 | 生物(RNA-seq)+控制(状态估计/MPC)+数据科学+机器人(模仿学习) 混聚；`("科学家",)` 兜底 |
| 后端开发工程师 | 198 | 189 | 混入 AI 技能(LLM/AGENT/数据管道)，主体仍后端，失真中等 |
| 顾问 | 20 | 10 | 纯兜底词，IT/数据分析/云计算顾问混聚 |
| 研究员 | 15 | 6 | 泛词兜底（AI/ML/数据），语义宽泛 |
| 硬件工程师 | 7 | 84 | 电源(LLC拓扑)+音频(扬声器音腔)+芯片验证(LVS/DRC) 混聚 |
| 解决方案工程师 | 6 | 59 | Adobe营销CMS+昇腾/DPDK(NVIDIA加速)+嵌入式固件+Magento 混聚 |
| 产品经理 | 6 | 34 | 医药供应链(GxP/冷链)+数据工程(dbt/Airflow)+AI(RAG) 跨行业混聚 |
| 数据库管理员 | 4 | 49 | 测试技能(功能/API/回归测试)+Oracle EBS+数据库 混聚 |

- 相对正常：前端开发工程师(313)/数据分析师(137)/Java开发工程师(267)/全栈工程师(239) 主体方向一致，仅少量混杂，可接受。
- **附带发现**：多个节点存在 `weight/source_count` 为空的边（w=-，如数据库管理员测试技能、前端 ERP、软件开发 PowerShell/HRSD）——非聚合流程写入的边，需单独立项核查来源。
- **处理方向（已实施 2026-08-09，同算法工程师）**：优先解决归一化兜底词（软件/科学家/架构/顾问/研究员/硬件/解决方案），改按 JD 技能内容聚类归位。
- **兜底词治理落地（2026-08-09）**：`dictionary.py` 中失真兜底族（软件开发工程师/科学家/架构师/研究员/顾问/硬件工程师/解决方案工程师/专家/算法工程师）不再作为聚合目的地——`_GENERIC_ROUTED_FAMILIES` 拦截后由 `_POSITION_SKILL_ROUTING` 按 JD 技能路由到细分族（视觉/自动驾驶/大模型/语音/机器人 → 通用算法 → 大数据/数据分析/前端/后端/DevOps/网安/测试/嵌入式/数据库 → 语言族兜底）；无技能或未命中路由返回空串（不入图，与泛词停用词口径一致）。`normalize_position_name(name, skills=None)` 新增 skills 参数（默认 None 兼容旧调用），调用点：tasks.py 三处（batch_extract 快照 / discovery_daily 聚合 / cross_validate 写回）、cross_validate.py 与 scripts/cross_validate.py 分组、kg_service.py import_jd 两处（二次归一化）、aggregation.py build_aggregates/_inflation_stats。**注意**：kg_service 与 aggregation 的归一化必须同步传 skills，否则 batch_extract 已路由的"算法工程师"会被二次归一化清空（2026-08-09 修复）。`scripts/reposition_generic_positions.py` 保留为回归校验工具（兜底族恒为空输出；算法工程师特判：纯通用算法技能合法归位不算缺口）。存量数据：候选池 5 条兜底族 candidate 记录（软件开发工程师/架构师/科学家/硬件工程师/研究员）已于 2026-08-09 直接删除（用户选择，未走 rejected 审计，因兜底族名已不可能再入池）；Neo4j 旧快照中的兜底族节点不再增长。
- **兜底词治理效果（2026-08-09 全量聚合后）**：执行 aggregate_positions（53 positions / 2409 edges / 移除 439 低频边）。算法工程师 486→73（-413），细分族显著增长：大模型算法 12→185、机器视觉 8→80、Python 117→168、机器人 4→25、后端 198→229。无路可归 831 条（无技能 416 / 未命中路由 415，多为国际源英文技能未覆盖与冷门方向，符合不入图决策）。对比报告见《岗位治理对比报告_20260809.md》。
- **算法工程师二次细分（2026-08-09 晚）**：审计发现算法工程师节点仍混聚方向技能（目标检测 8/图像处理 5/NLP 8/transformer 6/ROS 5 等），因细分规则未覆盖这些词被通用算法族吸住。增强 `_POSITION_SKILL_ROUTING`：机器视觉规则加目标检测/图像分割/图像处理/ocr，大模型规则加自然语言处理/nlp/transformer/生成式ai/aigc，机器人规则加 ros（模拟 108→75 分流，英文词均 ASCII 词边界匹配防误吸）。重聚合后算法工程师 freq 73→51（纯通用算法）。
- **僵尸节点清理（2026-08-09 晚）**：8 个无数据支撑的兜底族节点（软件开发工程师 319/架构师 35/科学家 32/研究员 15/硬件工程师 7/解决方案工程师 6/顾问 20，专家节点不在图中）DETACH DELETE 删除，Position 59→52。**注意**：cleanup_graph.py 的 merge_positions 会用 normalize_position_name(name) 无 skills 归一化，兜底族名返回空串全删（含合法算法工程师节点），勿直接跑它删兜底族——需精准指定节点删除。
- **孤立技能清理（2026-08-09 晚）**：审计 408 个无 REQUIRES 入边的孤立技能，全部有 EVIDENCED_BY 证据（从真实 JD 抽取，因僵尸节点删除失去唯一岗位引用，非幻觉）。其中 132 个在 SKILL_WHITELIST（人工重点技能库，保留），276 个白名单外已删除（连带 281 个 EVIDENCED_BY 关联边），Skill 1504→1228，孤立残留 0。5 个有 LEARNABLE_VIA 课程引用（OpenAI API/Web Services/Cybersecurity/Unreal Engine/OpenShift）其中 4 个在白名单内保留。删后孤立 Evidence 180 个（无入边，历史审计留档未动）。**教训**：孤立技能 ≠ 可删，必须先查白名单与证据/课程关联。
- **孤立 Evidence 清理（2026-08-09 晚）**：删除 180 个完全孤立 Evidence 节点（无入边无出边），Evidence 2837→2657，孤立残留 0。删除依据：①完全孤立不被任何 Skill/Position 引用（产品证据查询走 Skill-EVIDENCED_BY，命中不到）；②其 source_url 180/180 在 jd_raw 表有原始记录，图内 Evidence 仅是原文冗余副本，删除不影响审计链。**教训**：Evidence 是 jd_raw 的图内冗余副本，判断可删性只需确认 source_url 在 jd_raw 仍有记录即可。

## 2026-08-13 确认记录（用户确认，AGENTS.md §7 落点）

- **算法核心变更已确认（用户 2026-08-13）**：JD prompt 标题优先迭代（feat/algo-jd-title-priority，prompts.py 规则 1「标题优先」「技术栈前缀保留」+ few-shot 示例 11，skills F1 0.762 基线持平、Odoo 实证修复）；学习路径全套变更（feat/algo-learning-path-eval：评审定稿口径、课程名语义门控 ≥0.5、课程级兜底 0.55、学时分层类别基准 + weak 减半、脏边清理 1854 条、审查修复）——合并前仍建议张恺天过目（红线流程），但用户已确认变更方向。
- **openpyxl>=3.1 dev 依赖已确认**（评测链盲标工作簿生成用，pyproject.toml dev-dependencies）。
- **图谱脏边清理已确认**：删 1854 条严重脏边（sim<0.3，LEARNABLE_VIA 4415→2561），备份 reports/learnable_via_deleted_*.jsonl；**可疑档（0.3-0.45，1232 条）抽审 30 条结论：约 2/3 为合理弱相关（Supervised Learning→ML with Python 0.446 等），删除会大量误删——保留，不清理**。
- **盲审 gold 口径补充**：r2_001 采集元数据明示 5-10年/大专但正文未声明 → 按 round1 口径留空（终审可再定是否参考元数据）。

## 2026-08-15 图谱残留核查与清理（空权边根因 + 业务词技能）

> 起因：本文件 2026-08-09「附带发现」——节点存在 weight/source_count 为空的边（非聚合流程写入），需单独立项核查。08-15 完成核查、修复与存量清理（连接 192.168.0.140 真实库）。

- **空权边根因（核查结论）**：REQUIRES 空权边全部来自 **SimHash 重复记录（snapshot._duplicate_of）的独立入图残留**。链路：① 爬虫入库 → 阶段 2.5 dedup_simhash 标记重复；② batch_extract/import_jd 对**所有**含抽取记录入图（旧代码不跳过重复）→ 创建岗位节点（status 默认 candidate）+ Evidence + REQUIRES(necessity/level)；③ 聚合 build_aggregates **跳过**重复记录 → 该岗位永远不在聚合输出 → REQUIRES 永不获 weight/source_count，write_aggregates 的对齐删除也碰不到（只处理聚合输出内岗位）。重复记录的 canonical 记录岗位名多不同（AS400 应用程序 vs AS400应用、Endur技术 vs Endur 技术）或为空（MEMS/FBI 特工/应用AI工程总监→大数据开发工程师），故残留节点是"重复 JD 的残留名"，无独立信息。
- **修复（代码，三处口径对齐）**：① batch_extract 入图前跳过 `_duplicate_of` 记录（抽取结果仍落库推进游标，防重复 LLM 调用）；② dedup_simhash 标记后调用新增 `_purge_dup_import_residue(urls)`：删重复记录 Evidence 的 HAS_EVIDENCE 边 → Evidence 被技能 EVIDENCED_BY 引用则保留节点（证据链完整）否则连带删 → 受影响岗位无证据且 REQUIRES 全空权时 DETACH DELETE（纯残留）；③ rebuild_graph.py 早已跳过重复（同口径）。
- **存量清理**：7 个残留岗位（应用AI工程总监/AI与数据风险管理/MEMS设计与仿真/Endur 技术/AS400应用/AS400应用程序/FBI特工）+ 115 空权边 + 9 条 HAS_EVIDENCE 边删除；9 个 Evidence 节点**全部**被技能 EVIDENCED_BY 引用（29-51 条/个）→ 按证据链完整性保留。备份 `backend/reports/dup_residue_cleanup_20260815.jsonl`。清理后全图空权边 = 0。
- **业务词技能清理**：08-13 报告点名"费用/英语/日志"确认为存量残留（LLM 误抽 + 停用词未覆盖）。10 个节点（费用/资本费用计算/英语/英语四六级/英语沟通/英语口语/日志/日志监控/结构化日志/审计日志）删除，连带 REQUIRES/EVIDENCED_BY/SIMILAR_TO 边；`日志分析` 在白名单（合法技能）保留。备份 `backend/reports/noise_skills_cleanup_20260815.jsonl`。**防复活**：SKILL_STOPWORDS 补录 P6 批次 9 词（裸词"日志"已在 P5）——聚合 `_is_valid_skill_name` 消费侧同步拦截，存量 extraction 不再重建边。
- **教训**：① 去重标记（dedup 2.5）与入图（阶段 3）在**同一 ETL 内**先于抽取，但跨轮次的重复对（canonical 先入库、dup 后入库被后续轮次发现）仍可能"标记晚于入图"——凡"标记层跳过、消费层跳过"的口径，中间层（import_jd）必须同口径，否则残留永久化；② 图谱操作前先确认节点有 JD 支撑（HAS_EVIDENCE + jd_raw 非重复抽取）再判定"僵尸"——8 个 legacy 空证据岗位（AI 证据/Web/AI 与自动化 等）初判僵尸，实测有真实 JD 支撑 + 聚合覆盖（freq/weight 齐全），仅证据边历史缺失，不可删。

## 2026-08-15 遗留发现（未处理，待用户决策）

- **岗位可见性口径问题（需决策）**：import_jd 新建岗位默认 `status='candidate'`，而匿名/guest 仅见 emerging/stable/declining（graph.py `_PUBLIC_POSITION_STATUSES`）→ **绝大多数正常岗位（Java 426 频次/DevOps 81/后端 415/前端 433）对匿名用户不可见**，只有被 discovery 状态机提升的 3 个 stable + admin 审核的少量岗位可见。candidate 语义在"发现候选池"（PG discovery_candidates）与"import 占位"（Neo4j status）间混淆。
- **碎片岗位名治理（需决策）**：`AI 证据`/`Web`/`AI 与自动化`/`Gemini 应用合作伙伴` 等 9 个 legacy 岗位 + 大量 candidate 碎片（AI 原生构建/CMDB发现/GTM/IT 站点技术支持 等，各 1-2 条证据）是 LLM 抽取的岗位名碎片，有真实 JD 支撑但语义失真——需岗位名归一化治理（_POSITION_KEYWORDS/路由扩充或重抽），删除会丢图谱呈现，不建议直接删。
- **远程库运维注意**：192.168.0.140 的 ETL/重建会不定期运行（08-15 查询期间图谱证据边数曾变化），清理类操作前后需复核；代码修复需部署到远端 worker 才生效（当前远端仍跑旧代码）。

## 2026-08-15 岗位可见性语义修正（用户决策：保持匿名范围不变）

- **背景**：图谱 Position.status 承担双语义——发现状态机（candidate/emerging/stable/declining/archived/rejected）与 import_jd 占位默认值（candidate）。PR #93「岗位可见性分级」按"candidate 待审核不外宣"设计（匿名/guest 仅见 emerging/stable/declining），但 import_jd 把**所有**新岗位默认标 candidate → 137 个普通岗位（含 Java 426/前端 433/后端 415 等 26 个主流岗位）全部对匿名隐藏，匿名全景图仅 3 个 stable 岗位。
- **用户决策（2026-08-15）**：**保持现状不公开**——匿名可见范围不变（3 个 stable），只修正状态语义。（注：08-15 当日决策反转，PR #222 已把 active 追加进 `_PUBLIC_POSITION_STATUSES` 开放，见待办 T-07。）
- **实施**：① `PositionState` 新增 `ACTIVE = "active"`（图谱常态岗位，import_jd/聚合产生，非发现状态机成员）；② import_jd 创建岗位默认 `status='candidate'` → `'active'`；③ `_PUBLIC_POSITION_STATUSES` 08-15 当日追加 "active"（PR #222，决策反转）；④ graph.py status fallback 默认值 candidate → active；⑤ openapi/前端类型与颜色表同步（active 用蓝灰 #64748b，对齐 globals.css 设计令牌）；⑥ 存量迁移：图谱 151 个 candidate 岗位全部 SET active（先备份 `backend/reports/status_migration_20260815.jsonl`）——判定依据：persist 写入的镜像带 `state_updated_at`（3 个 stable 都有），151 个 candidate 全部无该属性（纯 import 占位；26 个名字与 PG 候选池重合属巧合，discovery_daily 只写 PG 不写图谱）。
- **迁移后状态**：active 151 / candidate 0 / stable 3 / legacy 9（legacy 碎片岗位不动，待碎片治理）；匿名可见岗位 = 3 stable（模拟 public 查询验证）。candidate 语义现在只由 persist 产生（发现候选镜像），图谱中为 0——语义干净。
- **注意**：26 个 PG discovery_candidates（state='candidate'）岗位名（后端开发工程师/全栈工程师/DevOps 等主流岗位在列）仍**待 admin 审核**——审核通过后 persist 会把图谱 status 从 active 直接 SET emerging/stable（persist 不校验图谱侧状态，无阻碍）。岗位可见性开放（active 入 public statuses）与碎片岗位名治理是后续待办。

## 2026-08-15 孤立课程核查（974/1320 无 LEARNABLE_VIA）——结论：正常状态，不可删

- **现象**：74% 课程（974/1320）无 LEARNABLE_VIA 静态边：edx 170/170、icourse163 799/823、coursera 仅 5/327。
- **根因**：LEARNABLE_VIA 边来自 course_raw.snapshot 的 `skills` 字段（爬虫采集时产出）；**icourse163/edx 爬虫不产出技能标签**（icourse163 仅 24/823 有、edx 0/170），coursera 爬虫产出（322/327）。历史课程即如此（非补采引入）。
- **产品影响：无功能缺陷**——`courses.load_courses_for_skill` 三级链路：静态边 → 技能级语义 fallback（有课技能池）→ **课程级语义兜底 `_semantic_match_course`（`_course_pool` 取全 Course 节点，标题语义匹配 0.55 + 标题门控 0.5 + 灰色带质量门控）**——孤立课程全在兜底扫描范围内，照样可被推荐。
- **不可删除**：08-15 补采的 415 门（edx 74/icourse163 337/coursera 4，crawled_at ≥ 08-14）几乎全部无 skills 标签——补采目的就是给语义兜底补课源（学习路径缺口治理），删除会直接减少兜底弹药。
- **不建议补静态边**：08-13/08-15 治理的"课程语义误配"（#192 灰色带门控/#198 graph API 门控）根因正是静态脏边（爬虫标签质量差）；补标签需防脏边，收益仅为图谱可视化/静态推荐效率。若未来要补：只能在爬虫层产出高质量标签（如 LLM 课程技能抽取 + 门控），勿手工批量建边。
- **教训**：图谱节点"孤立"≠垃圾——先查产品消费链路（本案例语义兜底覆盖）与数据源成因（爬虫缺字段），再决定清理/保留；课程节点尤其不可按"无入边"删（学习路径是语义匹配驱动的）。

## 待办清单（2026-08-15 立，来源：08-15 图谱治理会话）

> 按优先级排列；完成一项划掉一项（删除对应条目并在此行追加 ✓ 记录）。均为本次会话核查/修复中识别、尚未闭环的事项。

| 编号 | 优先级 | 事项 | 验收标准 | 来源 |
|---|---|---|---|---|
| T-01 | P0 | **代码修复部署到远端**：tasks.py（batch_extract 跳 SimHash 重复 + `_purge_dup_import_residue`）、dictionary.py（P6 停用词 9 词）、kg_service.py（import_jd 默认 active）、graph.py（fallback active）、schemas.py（active 枚举）、openapi/前端类型颜色——当前 192.168.0.140 远端 worker 仍跑旧代码 | 远端 ETL 跑一轮后：无新空权边产生、新岗位 status=active、业务词技能不重建 | 空权边根因/可见性修正 |
| T-02 | P0 | **本地改动提交 → PR 合入** ✅ 已完成（08-15 16:36-16:41）：**#216** fix(be) SimHash 重复残留治理 / **#217** fix(algo) 停用词 P6 / **#218** fix(be) active 状态 / **#219** docs 审计同步——4 PR 全部 CI 全绿合入 develop | 4 PR 合入，CI 全绿 | 08-15 全会话 |
| T-03 | P1 | **26 个发现候选审核** ✅ 已完成（08-15）：24 个置信度 ≥0.6 且源 ≥2 晋升 emerging（含 后端/全栈/DevOps/算法/数据科学家 等主流岗位，复刻 admin review 链路：PG state + 图谱 status + audit_logs 24 条，备份 t03_candidate_review_20260815.jsonl）；**产品助理/AI/ML（final=0.55 <0.6）保持 candidate 继续观察**；匿名可见岗位 3 → 27（emerging 为公开态，审核通过即对外发布） | 候选池无长期滞留 candidate，主流岗位进入公开态 | 可见性修正核查 |
| T-04 | P1 | **碎片岗位名治理** ✅ 已完成两批（08-15，PR #220）：第一批 P7 停用词拦截 + 映射归位 + 存量清理脚本（IT 系统管理员保留、首席统计师映射撤销）；第二批 AI 泛词族按技能路由（`_GENERIC_ROUTED_FAMILIES`） | 碎片名映射到规范岗位或重抽后消失；`normalize_position_name` 覆盖 | 脏节点扫描 |
| T-05 | P1 | **孤立课程爬虫技能标签** ✅ 已完成（08-15，PR #221）：`enrich_course_skills` 对新采集课程做 LLM 技能抽取 + `is_valid_skill_name` 门控，写回 `snapshot[skills]`，load_courses 随之建静态边；存量 974 门孤立课程不动 | 新采集课程带 skills 标签且经门控；存量 974 门孤立课程不动 | 孤立课程核查 |
| T-06 | P2 | **孤立技能池评估** ✅ 已完成（08-15）：评估报告产出 + 停用词残留清理 26 个 | 评估报告：白名单内保留数/可删数，必要时清理 | 图谱健康检查 |
| T-07 | P2 | **岗位可见性开放** ✅ 已完成（08-15 决策反转，PR #222）：用户 08-15 决定开放，`_PUBLIC_POSITION_STATUSES` 追加 "active"（c575bc4），匿名可见范围扩大至全部有支撑岗位 | 开放时模拟 public 查询验证岗位数 | 可见性修正（用户决策） |
