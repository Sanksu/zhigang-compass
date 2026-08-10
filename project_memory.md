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
