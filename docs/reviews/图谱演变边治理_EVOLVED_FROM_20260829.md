# 图谱岗位演变数据治理 — EVOLVED_FROM 边清理（2026-08-29）

> 执行：AI 辅助（用户 tangyao 授权）；线上实例 192.168.0.226。
> 目标：治理 EVOLVED_FROM 岗位演变边，使其能准确支撑「展示一个岗位的演变」。
> 执行纪律：**先完整备份，dry-run 清单审核通过后执行**。

## 一、数据现状（治理前）

| 数据源 | 值 |
|---|---|
| Neo4j Position 节点 | 148 |
| EVOLVED_FROM 边 | 24（其中自环 10、跨域噪声 4） |
| graph_versions 快照 | 23 版（08-02 ~ 08-27，岗位 46→142，技能 475→4423） |
| evolution_events | 20 条 |
| jd_raw | 10273 |

岗位演变展示链路（`/evolution/{position}/evolution`、`/positions`、`/events`）数据基础完整，主要质量问题是 EVOLVED_FROM 边存在自环与跨域噪声。

## 二、治理内容

### A. 删除 10 条自环边（source.id == target.id）

根因：岗位演化推导算法（`derive_evolved_from`）在「同一岗位节点在新旧快照中名字相同/微变」时，rename/split 判定命中后对**同一节点**建了自环边。两端均为 legacy 残留节点（freq≤1），无任何演变含义。

| 岗位 | id | 类型 |
|---|---|---|
| CFD分析 / 仪器AIT / SAP 技术管理员 / OBD标定 / Web / AI 数据科学机器人教练 / AS400 应用程序 / UX / AI 生产力 / AI与数据风险管理 | pos_3122/3148/3188/3164/3153/3107/3173/3230/3112/3190 | rename/split |

### B. 删除 4 条跨域噪声 split 边（人工甄别）

根因：`_shared_segments`（共享 ≥2 个连续中文片段）把无关岗位误判为演化，目标均为 legacy 残留。

| source (active) | target (legacy) | 依据 |
|---|---|---|
| SeniorMeatCutter | Kubernetes与OpenShift | 屠宰工→K8s，完全无关 |
| TeamCenter基础设施管理员 | Kubernetes与OpenShift | 西门子 PLM→K8s 跨域 |
| TeamCenter基础设施管理员 | SAP 技术管理员 | PLM 运维 vs SAP 运维，跨域 |
| TeamCenter基础设施管理员 | AI 基础设施 | PLM→AI 跨域 |

### C. 岗位名归一（强变体对已合并，其余仅记录）

**已执行合并**（AI 数据科学机器人教练强变体对，同步迁移 EVOLVED_FROM 演变边保证演变链路不丢语义）：
- 主保留：`AI 数据科学机器人教练`(pos_3107, legacy, freq=1, 6 REQUIRES, 2 HAS_EVIDENCE)
- 被合并：`AI数据科学与机器人教练`(pos_3147, legacy, freq=0, 无 REQUIRES/HAS_EVIDENCE)
- 迁移明细：
  - 出边 `pos_3147→AI与数据风险管理(pos_3190)` → 迁为 `pos_3107→pos_3190`
  - 入边 `pos_3190→pos_3147` → 迁为 `pos_3190→pos_3107`
  - 两节点间的互边（pos_3107↔pos_3147）随 DETACH DELETE 自然清除
  - 合并后 freq 累加（1+0=1），pos_3147 删除
- 验证：pos_3147 已删（exists=0），pos_3107 演变网络完整（→AI与数据风险管理，AI与数据系统→，AI与数据风险管理→）

**继续合并 4 组变体对**（主节点保留原则：freq/REQUIRES 边多/active 优先；被合并节点均无 EVOLVED_FROM 演变边，仅迁移 REQUIRES→Skill/Tool + HAS_EVIDENCE）：

| 组 | 主保留（active） | 被合并 | REQUIRES→Skill | Tool | HAS_EVIDENCE |
|---|---|---|---|---|---|
| CT技师 | CT技师(pos_3263, freq→3) | 首席CT技师(pos_3292) | 3 | 0 | 1 |
| STEM讲师 | STEM科技教育讲师(pos_3220, freq→2) | 课后STEM讲师(pos_3143) | 4 | 0 | 1 |
| ECMO | ECMO项目(pos_3309) | ECMO项目协调员(pos_3307) | 0 | 0 | 1 |
| FPGA设计 | FPGA设计研究(pos_3297, freq→2) | FPGA设计(pos_3232) | 19 | 4 | 1 |

验证：4 个被合并节点均删除、主节点边完整迁移（CT技师 REQUIRES 5→18，FPGA设计研究 35→58，STEM科技教育讲师 12→21）。

**仅记录不合并**（其余变体，保留现状）：
- AI 基础设施(pos_3070, legacy) / AI基础设施工程师(pos_3235, active) —— 以 active 为准，legacy 由 EVOLVED_FROM 维护
- AI与数据风险管理(pos_3190, legacy) / AI与数据系统(pos_3293, active) —— 已由 EVOLVED_FROM 串联，保留

**移动端 + 鸿蒙系列合并 2 组**（技术栈细分——前端/全栈/安全/原生保留维度，仅合并命名变体 / 被包含超集）：

| 组 | 主保留（active） | 被合并 | REQUIRES→Skill | HAS_EVIDENCE | freq→ |
|---|---|---|---|---|---|
| 移动端 | 移动端工程师(pos_3227) | 移动开发工程师(pos_3082) | 9 | 5 | 1→4 |
| 鸿蒙 | 鸿蒙全栈工程师(pos_3135) | 鸿蒙前端开发工程师(pos_3062) | 3 | 4 | 2→3 |

验证：被合并节点均删除、主节点边迁移（移动端工程师 REQUIRES 22→29 / HAS_EVIDENCE 1→6，鸿蒙全栈 REQUIRES 21→24 / HAS_EVIDENCE 3→7）、EVOLVED_FROM 保持 6 条。

**保留的技术栈细分**（不合并）：
- 移动前端开发工程师(pos_3095) —— 纯前端技术栈（CSS/Vue/React），与原生移动不同域
- 移动端全栈工程师(pos_3121) —— 全栈维度，保留
- 移动网络安全工程师(pos_3087) —— 安全细分，保留

**追加合并 3 组**（近似名扫描甄别，技能高度重叠的命名变体）：

| 组 | 主保留（active） | 被合并 | REQUIRES→Skill/Tool | HAS_EVIDENCE | freq→ |
|---|---|---|---|---|---|
| GEO优化 | AIGEO优化(pos_3265) | GEO优化师(pos_3264) | 3/0 | 1 | 1→2 |
| AgenticAI | GenAI/AgenticAI(pos_3298) | AgenticAI交付(pos_3296) | 12/6 | 1 | 1→2 |
| SAP | SAP集成(pos_3193) | SAP扩展(pos_3194) | 20/3 | 1 | 2→3 |

验证：被合并节点均删除、主节点 REQUIRES 完整迁移（GenAI/AgenticAI 37→59、SAP集成 35→53）、EVOLVED_FROM 保持 6 条。

**保留不合并**：IPQC/QC（巡检 vs 化验/药典，不同领域）；前端/后端/算法/分析师等系列为合理技术栈/领域细分。

**清理孤立幻觉 Skill 节点**（白名单外 + 完全孤立，复用 cleanup_graph 口径）：
- 删除 `Gross Profit`(sk_113083)、`眼动追踪`(sk_113717) 2 个无任何关系的孤立 Skill（前者为财务指标误抽，后者无岗位引用）
- 验证：残留孤立 Skill = 0

**降级无 JD 证据的 active 岗位**（freq=0 且 jd_raw/最新快照均无记录）：
- 降级 5 个为 legacy（保留 REQUIRES 技能边不破坏引用）：DOB审批协调员(pos_3311)、ECMO项目(pos_3309)、IRA协调员(pos_3312)、PMC生产计划员(pos_3310)、TMS技师(pos_3308)
- 依据：jd_raw 零记录 + 最新快照无此岗位 + freq=0
- 验证：freq<=0 的 active 岗位残留 = 0；岗位状态分布 active 90 / stable 25 / legacy 17 / rejected 3 / emerging 3（合计 138）

**同步生成最新图谱快照 `graph_v20260829`**（反映最终治理后图谱状态，覆盖更新）：
- 经 `GraphVersionManager().create_snapshot(triggered_by="manual-gov")` 在 worker 容器执行（与 snapshot_graph 同路径）
- 最终快照字段：positions **135** / skills 4629 / evidence 5436 / evolved_from_edges 6 / **data_warning = null**
- 治理后最终更新（vs graph_v20260827）：node_added 301 / node_removed 2692 / node_changed 0（含 Evidence 去重 2676 + 噪声岗位 3）

**删除招聘广告话术噪声岗位**（3 个，无技能边 + 无业务引用 + 名字为薪资/福利体现）：
- `smt车间操机岗位普工月薪6k可晋升工资可预支`(pos_3258)、`上市新能源直招入职五险一金非流水线6k保底`(pos_3256)、`中创新航月7000/包吃住`(pos_3257)
- 依据：discovery_candidates/evolution_events 均无引用，HAS_EVIDENCE 仅指向证据，无 REQUIRES 技能边

**Evidence 重复去重**（同 source_url 保留最新一份，迁移引用边后再删除，避免破坏证据链）：
- 发现 2588 个重复 URL / 2676 条重复份（占原 Evidence 8089 的 33%）
- 删除 2676 条重复，**迁移 2648 条引用边**（HAS_EVIDENCE/EVIDENCED_BY 重连到保留份）
- 验证：0 重复 URL 残留，Evidence 8089→5436，HAS_EVIDENCE 4905 / EVIDENCED_BY 44742 关联保留

**清理 5 个完全孤立的幻觉 Skill**（无 REQUIRES 入边 + 无任何关系）：`ADAS`、`土建基础`、`支架安装`、`电气施工`、`调试并网`（后 4 个为施工工序误抽）。验证孤立 Skill 残留 = 0。

**最终快照 `graph_v20260829`**（覆盖更新，与图谱一致）：positions 135 / skills 4617 / evidence 5436 / evolved_from 6 / **data_warning null**。

**删除 7 个黑名单技能**（SKILL_STOPWORDS，用户确认接受删除证据链）：
- 多模态/性能调优/操作系统/模型微调/模型部署/计算机网络/车联网（cleanup_graph.filter_skills 口径 DETACH DELETE）
- 随删 1 条 REQUIRES 入边 + 375 条出边（技能关系/证据），Skill 4624→4617
- 验证：图内黑名单技能残留 = 0

**扫描结论（保留不处理）**：
- 1254 个仅 1 条入边的低频技能——项目 cleanup_graph 明确不删低频（与聚合口径冲突），保留
- 同名 Skill 重复 = 0（name 唯一）

## 三、执行结果与验证

| 项 | 治理前 | 治理后 |
|---|---|---|
| EVOLVED_FROM 边 | 24 | **6** |
| 自环边 | 10 | **0** |
| Position 节点 | 148 | **135** |
| API 健康 | — | health HTTP 200 |

> EVOLVED_FROM 由 10→6 的说明：第一次治理后 10 条中含 4 条涉及 `AI数据科学与机器人教练`(pos_3147)。合并 pos_3147→pos_3107 时，其中 2 条互边（3107↔3147）删除、2 条（3147→3190 / 3190→3147）合并进已有边（3107→3190 / 3190→3107），最终净剩 6 条全部为 AI 域合理演变，无丢失。

治理后剩余 6 条 EVOLVED_FROM 全部为 AI 域活跃岗位的合理演化（AI 数据科学机器人教练 / AI与HPC可观测性 / AI与数据系统 / AI与数据风险管理 / AI基础设施工程师 / AI 基础设施），无自环、无跨域噪声，可供「展示一个岗位的演变」准确串联。

## 四、备份（可回滚）

备份留在 226 `~/zhigang-backup-20260829/`：
- `neo4j_data_20260829.tar.gz`（40MB，完整 Neo4j 数据卷含 WAL）
- `zhigang_pg_20260829.dump`（114MB，PostgreSQL custom 全库备份）

## 五、后续建议

1. **算法防复发**：`derive_evolved_from` 建边前增加自环防护（`WHERE a.id <> b.id`），避免`同节点 rename/split`再次产生自环边。属算法增强，需人工 Review。
2. **岗位名归一**：AI 数据科学机器人教练强变体对已合并（同步迁移演变边）；其余 legacy 变体（AI 基础设施/AI基础设施工程师 等）保留现状，如需再合并须先评估演变网络影响（本轮仅合并强变体对）。
3. 每日 ETL 会基于最新两版快照增量推导 EVOLVED_FROM，历史清理不影响后续正常推导；但若需彻底重建干净的历史演变边，可考虑按版本全量重跑（重操作，需专项评审）。