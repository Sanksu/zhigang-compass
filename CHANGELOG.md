# 变更日志（CHANGELOG）

> 智岗罗盘——多源异构驱动的岗位能力动态演化与人岗匹配系统（XH-202621）
> 项目周期：2026.07.13 — 2026.09.05。本文件按里程碑汇总主要变更（git 历史 620+ commits / 190+ merged PR）。

## M5（2026-08-26 — 09-04）：交付冲刺

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
