# 变更日志（CHANGELOG）

> 智岗罗盘——多源异构驱动的岗位能力动态演化与人岗匹配系统（XH-202621）
> 项目周期：2026.07.13 — 2026.09.05。本文件按里程碑汇总主要变更（git 历史 620+ commits / 190+ merged PR）。

## M5（2026-08-26 — 09-04）：交付冲刺

### 2026-08-22
- **学习路径课程推荐治理三连：误配拦截 + 链接修复 + 无课不误导**（#405/#407/#408）：用户实证三类问题（Airflow→航空气象学、PostgreSQL→MySQL、icourse163 链接跳错误页）全链路闭环——**#405** icourse163 课程 URL 补学校简称前缀（`/course/{shortName}-{courseId}`，纯数字路径服务端 404→commonError.htm，存量 891 门全坏；`backfill_icourse163_urls.py` 从 raw_text 提取 shortName 回填 PG+Neo4j **783 条**，108 条缺 shortName 的培训类噪声课跳过）；**#407** 课程推荐跨语言无词面交集门控 `_CROSS_LANG_NO_OVERLAP_SIM=0.75`（技能名纯 ASCII × 标题含中文 × 无词面交集时 sim 需 ≥0.75 且质量分不豁免——实证误配 Airflow↔航空气象学 0.6632 超灰带上限直通、PostgreSQL↔MySQL 课 0.548；原 P1-1「PostgreSQL→MySQL 可救案例」口径经用户裁决废除，无一一致课程宁缺毋滥；单测 30/30，50 案例 A/B course 维 0.874→0.838 掉分项即目标误配）；**#408** 前端无一致课程不渲染跳转链接（时间轴「前往学习」CTA 无 url 不渲染去死按钮、节点面板空 source_url 课程卡改纯文本）；**配套补采**：edx sitemap 关键词定向（postgresql/airflow）入 6 门真实课（密歇根大学 PostgreSQL 系列×5 + IBM ETL/Airflow 管道课）并跑 load/evaluate/enrich 三阶段入图，复验 course 维回补至 **0.857**（剩余差距为 VLSI/UART/Vivado 等嵌入式技能如实无课），线上 API 实测 Airflow→仅 IBM 管道课、PostgreSQL→密歇根系列×3。coursera 搜索页定向采集返回浏览模式热门课（与 edx 同款关键词失效问题，26 条无关课程入库但门控可滤，定向采集修复待办）。算法红线留痕：门控收紧经负责人拍板（用户 08-22 指示）+ 待张恺天知会
- **爬虫 reactor 回归发现与恢复**：08-22 三课程源定时采集全崩（`RuntimeError: installed reactor epoll ≠ asyncio`）——#353 引入的模块级 `from twisted.internet import reactor` 被 SpiderLoader 预加载抢先安装默认 reactor，**#404 当日已修但未进运行镜像**（镜像构建滞后于 develop 合并）；本次以容器内热修（拷入 #404 版 middlewares/zhilian）恢复采集并完成补采，镜像重建后永久生效。**部署教训**：api 容器代码经 `docker-compose.override.yml` 以 bind mount 透传主工作区 `backend/app`——重建镜像不切换主工作区分支则改动不生效，生产化部署须无 override 重建（本次 api 已按无 override 方式运行镜像代码）；worker 无挂载、镜像代码即生效
- **Z-score 占比口径归一化：评审三确认项闭环**：第五轮审查算法条目 A-1/A-2/A-3 经负责人拍板（①方案）随分支落地——**A-1①** 演化信号分子与分母同边集（均仅 REQUIRES，BELONGS_TO 等技能→技能边不再混入分子，占比可>1 问题消除，与 state_machine 过滤约定对齐；旧快照无边标签按历史口径兼容）；**A-2①** Z-score 序列整列同口径（全部窗口有占比分母才用占比，任一窗口缺分母整序列退回计数——堵住新旧口径混排致批量伪 declining 复活）；**A-3①** 检测侧消费 `GraphVersion.data_warning`（证据量萎缩<50%/膨胀>200% 的快照整期剔除，不作为 current 也不进 μ/σ，堵部分源故障反向伪 emerging；展示侧打标不剔除不变）。附三个评审指出的测试缺口回归（混合口径序列/部分源故障反向/非 REQUIRES 分子）；设计文档 §7.1、openapi EvolutionSignal 描述同步口径（SSOT），前端类型再生成。幻觉防控/演化算法红线留痕：负责人拍板 + 张恺天知会
- **第五轮全项目代码审查立即修批次**：基于《全项目代码审查报告_20260822.md》（develop 8f0f3d6 增量 +10,256 行，总评 B+）拍板清单 #1/#2 三处 P1——**P1-1** dict-guard 动态过滤跨容器断链修复（compose api/worker 补 `skill_filters_dynamic.json` 单文件挂载；`dynamic_filters._write` 弃 tmp+os.replace 改直接覆写——单文件 bind mount 上 rename 覆盖挂载点 EBUSY，与 runtime_settings 同口径，损坏由 `_load` 空层兜底；DEPLOY.md 补宿主空层文件预建引导）；**P1-6** `compute_confidence` 下界钳制（全零输入+孤立技能曾产出 −0.075 被 Schema `ge=0.0` 拒绝崩 discovery worker，实证路径）；**P1-7** `thresholds._get` 坏值按键回退默认（null/非数字单键曾致 int()/float() 裸抛 TypeError 停摆 SimHash/时滞检测全链，实证路径）。连带修 **dict_guard_gate 非确定性取样**（`next(iter(SKILL_STOPWORDS))` 随 PYTHONHASHSEED 抽中「微」/白名单重叠词致 CI 偶发红，改排序筛选纯停用词）。幻觉防控域改动按红线留痕：负责人拍板 + 张恺天知会
- **第五轮全项目代码审查报告落档**（#402）：docs/reviews/ 全项目代码审查报告_20260822.md，总评 B+/P1×7/Z-score 分支三条必须确认待张恺天裁决/拍板清单 12 项

### 2026-08-21
- **答辩演示优化五连**（#367/#369/#371/#372/#374）：图谱筛选改压暗式打标（`computeFilterMarks` 打标不剔除，布局与镜头稳定，顺带修复滑筛选条镜头跳回存量问题）；2D/3D 演示视角书签 `flyTo`（600ms 缓动飞行，锚定具名岗位簇，节点缺失自动隐藏）；简历匹配页两处裸 spinner 换 AI 生成感加载（`AiThinkingCard` 骨架+分阶段文案轮播 + overall_summary 打字机，reduced-motion 退化）；岗位级「已人工校验」Badge（契约 `PositionEditDetail.has_edit_log` 只读透出 PositionEditLog 存在性 + 审核草案「AI 生成」标注，人机协同可视化）；图谱大屏演示模式（focusMode 隐藏顶导/侧栏、画布 Card 同树切 fixed 不重挂载、详情栏转浮层、Esc 退出 + 浏览器 Fullscreen，新增 mock E2E 用例）
- **审查 P2 批次 + M4 收尾**（#376/#377/#378）：mockLogin 第三处硬编码口令改 env 注入+中性占位（M4 漏网之鱼）；删 use-graph-pan 死代码 + match/types 注释回流（L-14/L-15）；workers 23 处 print→logging + ETL 阶段失败聚合 send_alert + crawl running 态路径泄露源头修复 + CI rc==5 不再放行（L-5/L-6/L-9/L-16）+ discovery docstring 对齐实际晋升条件（A-3）；**连带修复**：归一化门禁裸调 send_alert 协程被静默丢弃（同步线程上下文）→ alerting 新增 `send_alert_sync`；M4 部署侧口令轮换已执行（backend/.env + 库内哈希同步重置，旧泄露口令实测 401 失效）
- **算法条目裁决闭环**（#381/#382，Issue #380 关闭）：项目负责人按建议方案裁决六项——**H5=B 口径对齐**（设计文档创新点表现状化：「计算+写回+展示已实现，硬门控为路线图」；**答辩话术同步此口径**）；A-2① skill_novelty 降级补 send_alert_sync 外送告警；A-4① 设计文档三处对齐 #318 不对称萎缩口径；A-6① 评测 FP 豁免拆分独立 `_eval_literal_hit` 纯词面判定（统计对守卫失灵恢复敏感）；L-13① 契约 trend 描述改 0..1 需求扩散度 + UI「扩散 N」中性标签去方向性暗示；裁决单六栏回填归档
- **第四轮全项目代码审查 P0/P1 修复三连**（#364/#365/#366）：基于《全项目代码审查报告_20260821.md》修复 5 项高危中的 5 项 P0/P1(除移交算法的 H5):
  - **#364 安全修复（H1/H2/M4/M8）**：简历解析缓存编辑改 copy-on-write（同字节文件共享时按用户 fork 独立 parsed_data，杜绝跨用户越权，H1）；生产 fail-fast 补 `debug=false 强制 + cors_origins≠["*"]`（SQL echo 不再泄 PII，H2）；E2E 凭据轮换为环境变量注入（M4）；语义模型预热失败降级时告警日志（M8）
  - **#365 前端数据诚实（H3/H4/M7）**：图谱节点详情删除哈希编造的需求%/趋势（H3）；匹配页删除 decorateGaps/GAP_COST 无条件重算，ROI/evidence/high_roi 直透后端 #341 契约字段（H4）；E2E mock 对齐 openapi 必需字段口径（M7）
  - **#366 测试与审计（M1/M5/M6）**：`tests/integration` 补 `pytest.mark.integration` marker 默认排除（杜绝本地 docker 数据耦合拉起，M1）；admin accounts 域四端点补 AuditLog（M5）；补 `test:coverage` 脚本并接入 CI，使 80/80/70/80 覆盖率门禁真正生效（M6，修正 README 宣称 95% 与阈值不一致）
- **匹配权重 BT v3：调优目标与验收口径对齐**（#363）：v2 权重由 Optuna 以 Spearman 为目标调出，与 Acc 验收口径（阈值 0.5 二分类）错位；以 Acc 为目标重搜（150+400 试验收敛同一最优），`configs/match_weights.json` 更新为 w=(0.669, 0.044, 0.287)/sim_threshold=0.938——384 对 v2 黄金集 **Acc 0.8281→0.8906**（fp 66→42、fn 恒 0）、Spearman 0.7642→0.7407；v1 300 对回归 Acc 0.9167→0.96；4 折按岗位分组 CV 一致。待决策（张恺天）：分类截断 0.5→0.58 可再达 0.9141（口径变更）；无 nice 技能岗位 nice_score 默认 1.0 的结构性白送分
- **core_duties 匹配器升级**（#362）：D2-A 整串子串对措辞变体脆弱（376 未命中 gold 中 70 条近失），命中判定升级为双向子串 ∪ 短侧字符 bigram 包含度 ≥0.7（补丁稿候选② Rouge-L 族确定性实现）；同批预测确定性重算微 F1 **0.2457→0.6017**；边界护栏单测防部分相关职责被吞
- **evaluation_110 归档刷新**（#361）：08-20 终跑误用早于 #328/#331 的过期 worktree 代码（归档含已删除的 Schema coverage gap 标记、缺六维/对齐口径）；以现行 develop 单跑重测刷新——skills raw F1 0.8825 / 对齐口径 **0.9629 达标口径有产物实证** / experience_accuracy 0.8889 / core_duties F1 0.2457（迭代前口径）
- **匹配评测 v2 口径可复现**（#360）：`evaluate.py --task match` 增 `--match-golden` 参数，生产 BT v2 权重在 384 对黄金集的数字首次有独立评测产物（此前仅 CHANGELOG/配置注释）
- **ETL 队列接入配置中心 + 调度迁移容器内 ARQ cron**（#348）：前端配置中心新增「ETL 队列」分区（`/admin/settings/etl`，批次上限/默认批次 + 每日调度时间）；`runtime_config` 新增 `etl_batch_cap`/`etl_structure_load_default`/`etl_validate_temporal_default`/`etl_run_hour`/`etl_run_minute`（openapi 契约 + 前端类型重生成）；`etl.py` 批次上限与阶段默认批次改读配置，新增 `run_etl_pipeline_scheduled` 容器内 cron 入口（当日幂等 Redis 锁，与 `etl_daily` 同语义），`settings.py` 注册 ETL cron（时间取配置，重启生效），替代外部 Windows 计划任务调度；后端 20 单测 + 前端 170 测试/lint/typecheck 通过
- **停用外部 05:00 ETL 计划任务 + 部署说明更新**（#349）：`scheduled_tasks.ps1` 移除 `ETLDaily` 任务（顺带修复 `$Tasks` 数组缺失逗号语法）、`crontab.example` 注释停用 `0 5 * * * etl_daily.py` 行；DEPLOY.md 新增 §6.1 ETL 调度说明（容器内 ARQ cron + 配置中心时间 + 幂等 + 重启 worker 生效）；团队启动指南/冷启动指南同步；本机已注销 `ZhigangETL_ETLDaily`（`etl_daily.py` 保留供 `--force` 手动重跑）
- **幻觉评测基座 + Grounding 防线消融（P0）**（#383/#384）：`grounding.py` 接入跨文档 NLI 矛盾检测（`nli_guard.py`，entailment/neutral/contradiction 三分类启发式 + 软门控），大模型生成解析与图谱检索发生蕴含冲突时强制重采样/回退；配套消融评测框架（`tests/evaluate/run_grounding_ablation.py`，确定性幻觉黄金标准覆盖全 NLI 信号类型）输出「无 Grounding 控制 vs 开启完整防线」拦截率对比，纯 Python 生成瀑布图 `data/evaluate/ablation_waterfall.svg`（免 matplotlib 依赖，答辩素材）
- **置信度标量化：证据距离优先 + 阈值校准（P1）**（#385）：`confidence.py` 在三维基础置信（JD 数/源多样性/增长率 + 学术社区加成 + Wilson 冷启动兜底）之上融合图谱接地距离 `graph_grounding_score` 与 LLM Logprob（信号缺失以中性 0.5 兜底），输出 0-1 标量；低于 0.75 自动流转 `candidate-review-tab` 阻断并复核（「需复核」Badge + 低分优先排序）；新增 `citation-badge.tsx` 可视化引用溯源角标组件
- **灰名单验证区：新兴技能漏召回兜底（P1）**（#386）：`dictionary.py` 新增运行时内存注册表——白名单未命中且非噪音的新技能进入验证区（命中频次积累 + 人工 graduate），缓解白名单对新兴技术反应滞后导致的漏召回
- **演示准备：领域语义黑名单扩充 + 学习路径拦截（P1）**（#387）：`domain_sem_blocklist.json` 扩充跨域黑名单对（量子计算×占星术 / 人工智能×占星术 / 金融×命理 / 医疗×玄学）；学习路径生成命中即拒绝并返回 `block_reason`（openapi 契约 + 前端 `resume-match-page` 展示阻断警告）
- **数据质量阈值配置化**（#388）：simhash / temporal_detector / embedding 语义去重阈值集中到 `data_quality/thresholds.py` 配置加载（运行时调整，不再改码）
- **dict-guard PR-B：技能字典自治守卫**（#390）：LLM 每日评估图谱数据（`dict_guard` 表 + worker + 每日 cron），分级调整技能字典过滤

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
