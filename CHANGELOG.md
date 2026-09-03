# 变更日志（CHANGELOG）

> 智岗罗盘——多源异构驱动的岗位能力动态演化与人岗匹配系统（XH-202621）
> 项目周期：2026.07.13 — 2026.09.05。本文件按里程碑汇总主要变更（git 历史 1727+ commits / 558+ merged PR）。

## M5（2026-08-26 — 09-04）：交付冲刺

### 2026-09-02
- **学历维度计入综合得分明细展示**：契约先行在 `MatchResult` 增加顶层 `edu_score`（第四维 BT v4，任一侧学历层级无法映射为 null，不参与总分加权），`weights` 增加 `edu`（w_edu 凸组合权重，未配置/非法为 null）——`score_position` 构造结果时透出 `edu_score`，compare 响应汇总 `weights.edu`；前端「综合得分」明细卡新增「学历背景」条（与必备/加分/经验并列，展示分数与权重，无信号时隐藏）。含既有 matching 219 + jd_match/api smoke 用例全通过。
- **技能解释 LLM 补齐修复 + 治理接口 + graph 前端优化（PR #762）**：① **补齐接口崩溃修复**（TypeError: object list can't be used in await）——`repository.query_all_skills` 系同步函数却被误 `await`，列表端点与补齐端点双双 500，去 `await` 恢复；② **补齐失败校验误报修复**（算法质心）：模式返回纯文本而未发 tool call 时 instructor 判空列表致 5/条失败——`LLMProviderChain` 新增裸文本同步路由 `call_text_sync` + `_call_provider_text`（不经 instructor 结构化校验，复用异常映射/熔断/退避语义），补齐改走该路由；③ **补齐批量 50→200**（前端可多次触发）；④ **client 缓存复用（方案 A）**：新增独立 `_raw_client_cache` + `_get_raw_client` 缓存原生 SDK client（instructor client 强制要求 response_model 不可用于裸文本），连接复用对齐 `_build_client` 原则，容器内实测二次命中不新增；⑤ 配套新增 skill_descriptions / skill_noise 治理接口与 LLM 决策记录、graph 技术栈/岗位画像动画与拖拽推开、节点详情面板增强；openapi 契约 + api.d.ts 再生成。**算法红线留痕：llm_provider.py 改动（call_text_sync/_call_provider_text/_get_raw_client）待张恺天复核合入**
- **同岗位下各 JD 匹配评分明细（下拉逐条查看）**：契约先行在 `MatchResult` 增加 `jd_breakdown`（该岗位各 JD 按分降序：jd_id/标题/三维分/命中数）与 `resume_id`，新增 `GET /match/result/{match_id}/jd/{jd_id}` 单 JD 详情（三维得分/差距/学习路径/原文，并入当前岗位详情）——`score_jd_compare` 现返回全部评分 JD 降序列表（原仅取最高分一条），新增 `score_jd_one` 按 id 评分单条 JD；前端比对详情头部加「切换 JD」下拉，切换即加载对应 JD 详情（岗位级字段域/权重/证据保持来源岗位）。含 3 条新单测，jd_match + api smoke 226 passed。

### 2026-08-29
- **画像证据回溯 + JD 数据管理（#662/#663，报告 docs/reviews/全项目代码审查报告_20260829.md）**：契约先行新增 4 组端点——`GET /graph/position/{id}/portrait-evidence`（薪资/经验/学历画像条目 → 支撑 JD 列表，口径镜像 build_aggregates：SimHash 重复/归档/岗位级通胀排除一致）、`GET /graph/jd/{jd_id}`（正文全文 + 出处链接，匿名可读）、`/admin/jd` CRUD（RBAC admin:* + AuditLog，编辑重算 content_hash 同 etl_tasks._build_jd_text 同源，抽取快照不动）；前端画像侧栏证据区 + JD 正文弹层 + admin-jd 管理页（/admin/jd，侧栏「JD 数据」）。
- **第七轮全项目审查（#664）**：8ba335f→5133f23 增量 203 commits；总评 B+ 无 P0，P1×7——画像证据通胀分母口径漂移 / jd_admin 绕过 resolve_operator / PUT 裸 dict / guest 可见 candidate（discovery+evolution 对齐 visibility 单一事实源）/ admin-jd openDetail 竞态与错误不可见 / jd_admin 零测试；**当日修复批 #665 闭环**（含 tests/admin/test_jd_admin.py 21 用例）；P2×13 择要留档（q 通配符转义随批修）；匹配侧岗位名三口径并存交张恺天复核（P2-6 算法红线）。
- **测试补充六组（#639~#644，166 例）**：proficiency 31 / KG 聚合 34 / semantic 26 / city_index 26 / cert_issuers 36 / llm_stats 13；配套修 semantic warm() 空白串过滤与 cert_issuers 未用 import；lfcs→LFCS 映射键勘误 + 泛化键（精算/日语）治理 + iso iec 键（#658）。
- **其他**：全审批池只读汇总端点 + 岗位审核页「总览」Tab（#659/#660）；图谱视图收敛（#652）与三视图视觉优化——职能域社区着色 + 环形布局标签象限（#661）；匹配详情最佳匹配 JD 原文展示（#654）；部署/技术文档补全（Code-Wiki 对齐当前代码 + DEPLOY 局域网运维实证 + 索引重建，#657）；图谱演变边 EVOLVED_FROM 治理留痕（#650）。

### 2026-08-27
- **前端展示整合优化（组件统一 + 信息架构重组，PR #587 / #591~#595）**：建 `components/shared/` 共享四件套——六态岗位状态徽标 `PositionStateBadge`（契约六态 + rejected 唯一源）、技能胶囊 `SkillChip`（must/nice/soft 三色）、刷新按钮 `RefreshButton`（统一 loading+icon）、统一指标卡 `MetricCard`（融合 evolution MetricCard 与 dashboard StatItem），Badge 补 `active` 变体。**收敛**：六态徽标（resume-match/node-detail/state-views/discovery 手写 label/tone 映射移除，graph 图例为 ECharts 颜色映射按 E2E 安全保留）、技能 chips 与刷新按钮（node-detail/resume-match/discovery：差距/路径/AI 诊断/重载等复用）、仪表盘关键指标接入统一 MetricCard（StatItem 去重）。**信息架构**：演化看板 3 Tab 化（信号 / 趋势与流向 / 版本与状态机，告警与指标卡跨 Tab 置顶）；settings 瘦身——爬虫配置（采集上限/限频/每爬虫开关与独立触发时间）迁入 admin/crawl「调度与限频」Tab（自包含 `CrawlScheduleConfig`），evolution+dictguard 合并「系统节流」分节并进侧栏，删 `/admin/settings/{crawl,dictguard}` 路由；admin-review 降 3 Tab，观察池/字典守卫迁独立路由 `/admin/review/{watch,dict}`（旧 `?tab=` 重定向兼容）；skill-aliases 并入 llm-decisions「动态别名表」Tab（`/admin/skill-aliases` 重定向）。全量前端单测 + typecheck 通过，E2E 相关页面未动。

### 2026-08-24
- **LLM 驱动灰度落地第一批（#492~#503 全部合入 develop，报告 docs/reviews/LLM驱动灰度验收_20260824.md）**：按「LLM 语义主导、确定性不变量守护」落六域决策信封——**决策底座**（`llm_decision_records` 单表 + 迁移 20260824_001 + 风险路由 R0 建议类/R1 低影响收紧/R2 解禁·归并·删除·关系一律人工/blocked 不变量失败，#492）；**调用运维**（invocation_scope 上下文 run/version/entity/env + 链总墙钟 + P95/P99 + purpose/model 完整性审计，测试/生产日志隔离，#493）；**JD 评测证据链**（新增 LLM-only 纯模型口径与 raw/aligned 三口径同时归档 + commit/gold SHA/provider/model/input_sha256 证据信封，避免达标数字掩盖纯模型回退；jd_raw.content_hash 指纹重抽，重爬更新正文/标题自动触发同条重抽并留 extraction_prev 归档，迁移 20260824_002，#494）；**名称归一**（岗位名/技能名 LLM 决策器，硬门防自创名·虚构标准名·同义反复，#495 + ETL 阶段 19 shadow 每日任务只落决策记录零生产写入，#496）；**分类**（技能分类提议每例同步落 skill_classify shadow 决策记录 + 岗位分类决策器（清单内强制选择防自创分类），#497 + 岗位职能域 LLM 命名接 cluster_label shadow，#498）；**治理**（dict-guard 统一风险路由 auto→R1/auto_applied、proposal→R2/proposal，证据/impact/provider/model 随档落库，++#499）；**技能关系**（类型/方向判定 + 防自指·虚构目标·方向不匹配硬门 + 先修环纯函数判定，#500；JD 共现候选→LLM 判定→proposal 决策记录脚本，环/门失败 blocked，#501）；**管理端**（契约先行 GET /admin/llm-decisions 分页过滤 + /summary domain×status 汇总，#502；「LLM 决策与验收」只读页——汇总卡 + 过滤列表 + RBAC，#503）。对齐 08-23 转向评估 No-Go 结论的反向落地：不绕过白名单/SBERT/Leiden/图约束/人工门禁，全部决策可追踪可回放，自动写图=0。剩余：分类/归一/关系黄金集冻结（三跑取中位）、关系审批执行通道、226 观察窗口
- **JD 解析验收评测正式通过（验收窗口提前执行）**（报告 docs/reviews/验收评测_20260824.md）：110 条黄金集三跑中位 **aligned F1 0.9629**（≥0.90 达标 +6.29pp），三跑极差 0.0004（远小于 ±0.01 非确定性带宽）、precision 满分×2、幻觉 FP 330 样本仅 1 条（#488 别名豁免生效）；停用词解禁/白名单扩容/双轮治理对验收指标零负影响。口径：aligned=词面真值对齐（豁免评测口径不对等），raw 0.8734 为精选对照非验收指标- **图谱治理：dict-guard 积压提案批量裁决闭环**（线上 192.168.0.226）：79 条积压提案（08-21 起，置信度均低于自动档 0.8）全部处置——**69 批准**（43 技能停用词+同名节点清理 / 19 孤岛课程删除 / 6 低质 LEARNABLE_VIA 边（sim 0.209<0.30 门控）/ 1 孤岛伪岗位）、**5 驳回**（Full Stack Development 应归一至「全栈」而非停用、BPEL/数据策略/智能建筑/网络布线为真实概念）、**5 移除停用词留算法岗**（多线程/数据库/数据结构/消息队列/缓存 解禁改匹配语义，当日已由负责人拍板解禁，见下）。执行走真实审批端点（DictChangeLog/AuditLog 全留痕）；图谱变化 Skill 4441→4399 / Course 1491→1472 / Position 139→138。**实证端点隐患**：`review_proposal` 图谱删除先于 PG 提交、非原子，当日由 #477 修复。报告归档 docs/reviews/图谱治理_20260824.md
- **全流程九环节体检**（采集→入库→ETL→图谱→匹配→演化→API→运维）：整体健康——三 JD 主源日更、技能归一化覆盖 100%、graph 四视图亚秒、embedding 三表随 ETL 新鲜。修复：宿主 crontab 死项（`rtk_force_restart.sh` 残留每分钟调用）清理（备份保留）、无引用旧 latest 镜像回收。孤岛课 528 = 采集入库但未建 LEARNABLE_VIA 边的活数据（诊断定论：496 门 T-05 设计内存量 + 93 门 7 天补全窗口内流转，产品无缺陷）。报告归档 docs/reviews/全流程治理_20260824.md（含次日上午勘误：体检时「ETL cron 配置正确」结论作废，根因见下条）
- **ETL 时区语义修正**（#478 已合+部署）：05:35 定时复查实证「每日 05:00」从未在北京时间点火——arq 0.28 cron 按进程本地时间触发且无 timezone 参数，容器默认 UTC 下 `etl_run_hour=5` 实为北京 13:00。compose api/worker 补 `TZ=Asia/Shanghai`（全库零 naive datetime 已核查无漂移风险）；226 应用重建后当日 09:08 手动补触发（14 分钟全链跑通、ETL 系报告首次落盘、indeed +58），次日 05:00 CST 起自然点火
- **dict-guard 审批端点原子性修复**（#477 已合+部署，postmortem 003）：review_proposal 重排为「operator UUID 前置校验 → PG 先提交 → 动态词表/Neo4j 删除后置」，副作用失败不回滚批准状态（effects_applied=false 透出）；rollback 端点补同款校验
- **核心 CS 词解禁（运行时 + git 固化 #483）+ 全栈别名归一**：负责人拍板批准 5 条 remove_stopword 提案（多线程/数据库/数据结构/消息队列/缓存——消息队列本就是白名单标准词却在停用词表自相矛盾），动态 protect 穿透即时生效，zkt 知会留痕；#483 从 SKILL_STOPWORDS 静态移除五词（git 固化，已部署实证）。**A/B 复测（A/B/A2 三跑，报告 docs/reviews/停用词解禁JD评测复测_20260824.md）**：精确率影响在 LLM 非确定性内（aligned F1 解禁 0.9601/0.9591 vs 停用 0.9597），唯一可归因成本=每跑 1 条「消息队列」幻觉（后定性为评测纯词面口径假阳性，#488 修复）；gold 含五词 31 次，停用态生产抽取静默丢失，维持解禁。**#479 已合+部署**：Full Stack Development 等全栈变体族归一别名至「全栈」（驳回停用提案的落地通道）
- **api_key_env 逐行安全审查通过并合入**（#459，审查记录见 PR 评论）：安全清单全过（密钥不落日志/回显、env 名双端正则、无新增认证面）；审查修复两处——`_build_client` 同源解析（env-only provider 此前真实调用链空 key 构建 client）+ 掩码 `*`+env 不再回捞旧明文（掩码原样落盘会被当显式明文压过 env）
- **webhook 告警格式自适应**（#480 已合+部署）：send_alert 原发裸 `{"event","message"}` 三大 IM 机器人平台均拒收（配置了地址也静默投递失败）；按域名自适应飞书/钉钉/企微 text 格式，其余 URL 保持通用 JSON。启用待 webhook 地址（226 backend/.env 一行 + recreate）
- **glassdoor 停用（可逆）**：查明每日都在跑但产出恒 0 字节——CDP Chrome 存活但两设计前置缺位（浏览器系统代理 226 v2ray 仅听 127.0.0.1 容器不可达 + 需人工过一次 Cloudflare 验证）；按 boss 先例 runtime config 停用（disabled_reason 写明恢复两步），历史 1134 条保留；226 同日更新至含上述全部修复的 develop 镜像并实证标记
- **图谱分类治理八连**（P0 技能分类 / P1 岗位域 / P2 卫生项，报告 docs/reviews/图谱分类治理_20260824.md）：① **批量 LLM 分类提议实跑**（#469 已合）：req_count 前 600 未分类技能 deepseek 并发提议 600/600 成功（高置信≥0.70 共 494）写 suggested_* 提议字段，权威 category 不动——补每日 ETL 审查（req_count≤3、20 条/日）只扫长尾的结构缺口；② **白名单扩容 605→1093 条、类目 23→20 归并**（#473 负责人拍板放行【四项裁决留痕：全量批准/归并批准/剔除确认/合并即部署】+ zkt 知会，已合并部署 226 并存量回填 491 节点，权威分类 564→1052、视图未分类 48.2%→8.3%）：实证并过滤两类语义陷阱（岗位族词表冲突「FPGA验证」会打穿 normalize_position_name 技能词守卫；大小写变体撞 normalize_skill 小写唯一），课程覆盖测试夹具随行换词；③ **岗位域语义命名 + 孤立岗兜底**（#470 已合，负责人拍板 + zkt 知会留痕）：线上重跑公开岗位域覆盖 69.6%→**100%**（123/123，15 无域岗均为 candidate 态正确排除），域名代表岗名→15 个语义职能域（数据分析师→数据分析、CT技师→医学影像技术）；④ **视图死属性 communityId 移除**（#471+#475 已合）：技能社区覆盖仅 6.9% 的遗留字段，前端/openapi/docs 零消费实证，图内 411 节点残留属性同步 REMOVE（#471 漏 add 两文件由 #475 补全）；⑤ **Tool 分类回退白名单词表**（#472 已合）：2670 个 Tool 空 category 清零（存量回填命中 229+哨兵 2117）；⑥ **dict-guard 门禁测试去抽样化**（#474 已合）：next(iter(白名单)) 哈希序抽样遇双重身份词即翻车的潜伏 flaky，扩容后暴露，改全量遍历+三类拒绝理由兼容；⑦ **评测幻觉清单别名感知豁免**（#488 已合）：MQ→消息队列「幻觉」逐样本定性为评测纯词面口径假阳性（正文含 MQ 合法保留），豁免与生产守卫口径对齐、真失灵仍浮现；⑧ **驳回冷却参数进管理面板**（#489 已合）：/admin/settings/dictguard 新分区 + RuntimeConfig 契约补齐 dict_guard 五键（铁律一欠账修复）
- **图谱治理轮次 2：当日积压清零 + 重复提案缺口修复 + 挂载分叉事故**（报告 docs/reviews/图谱治理轮次2_20260824.md）：① **33 条 pending 全处置**（31 批准：12 噪音/SQL 碎片停用 + 16 课程孤岛删除 + 1 低质边 + 2 状态丢失恢复（Teaching/Textile Industry）；2 驳回：数据策略/BPEL 重复提案），gold 证据裁决分水岭（SQL 碎片词 gold 收录 0 可停 vs 五词 gold 收录 31 次须保）；② **#485 重复提案缺口修复**：提案去重补驳回冷却（`dict_guard_reproposal_cooldown_days` 默认 7 天，pending 不重提/rejected 冷却不重提/approved 不阻塞），10 例单测；③ **⚠️ bind-mount 挂载分叉事故**：eval 恢复 mv 替换宿主文件 → 容器挂载仍指旧 inode → 14 条 blocked 写旧 inode 不可见丢失；容器内就地补写修复（双侧 68 条同步）。**教训：bind-mount 配置文件严禁 mv 替换，恢复须原地写回或重建容器 remount**
- **项目文档整理**：docs/README.md 索引全量重建（29 文件全覆盖，原仅 ~10）；技能字典自治守卫方案状态「实施中」→「已落地运行」（§9 分期表补真实 PR 号 #390/#392/#393/#394）；岗位名 LLM 审查方案状态同步（已实现·灰度默认关 #457/#460）；修复 test_cases.md 断链；新增 postmortem 003（dict-guard 审批非原子）

### 2026-08-23
- **LLM 驱动转向评估落档**（docs/reviews/LLM驱动转向评估_20260823.md）：结论 **No-Go**——治理/归一/关系生成尚不具备直接 LLM 化条件，先建黄金集与回归基线；新建私有仓库 Sanksu/zhigang-llm-driven 作主实验场（v0.1 架构反转方案已推 main），主仓库 hybrid 路线不受影响
- **P0/P1 批次六连**（#454/#455/#456/#457/#458/#460）：LLM 调用审计（provider 链埋点 + JSONL 明细 + Redis 聚合）、异步延迟重试链、配置加固；岗位名 LLM 审查落地**灰度默认关**（第四道防线 `position_review_enabled=False`，#457）；LLM 统计日报接入 ETL 阶段 17（#458）；M1 实验脚本（#460）；技能分类灰度（#461，负责人豁免审查留痕）。#459 api_key_env CI 绿但待人工逐行审查未合
- **闭环收敛四批**（#462~#467）：ETL 事实门禁（事实阶段失败不再发布快照/驱动演化）、诊断 GET 只读化 + PG 耐久回退、匹配结果 Redis 过期后从 match_results 回读回填、死表 skill_freq_observation drop（迁移 20260823_001）、embedding 三表增量回填（指纹游标消除每日全量重算）、课程/技能归一化增量化、/graph/panorama 物理删除统一至 /graph/view/{view_type}、死接口清理（/graph/position/{id}/skills 等）、演化信号同页双请求合并、技能归一化指纹 summary 改 JSON 字符串存取（Neo4j 属性不收 Map）
- **局域网部署固化**（192.168.0.226）：develop 镜像滚动更新（GHCR），5 容器全 healthy；icourse163 实跑 19 条产出实证爬虫代理三层修复（#443/#444/#445）生效

### 2026-08-22
- **工程设施 P0 收口（第五轮全流程评审建议当日执行）**：① `main` 分支补 GitHub 保护（1 人审 + 双 CI 必过 + enforce_admins + 禁 force-push/删除——此前 main 无任何服务端保护，「铁律二」仅靠自觉）；develop 保留 CI 门禁但关闭 force-push 与分支删除。② 在途分支收口：#417（dict-guard paged_ok 500）/#421（血缘含斜杠岗位名 + 证据链滚动）/#424（血缘缓存 + http 部署整页刷新丢登录）当日合并；Career Atlas 图谱工作台八连重构本地链（数据适配层/图层系统/节点档案/2D·3D 测绘语义）push 收口为 #428。③ 软技能部署两步补齐：21:58 重建镜像已含技能类别透出（容器内 grep 实证 6 处）+ `backfill_skill_category.py` 存量回填执行（补 4 节点，幂等复跑确认零待回填；3715 未分类为白名单外长尾属设计内）。④ 新增 `CD (images)` workflow：develop 合并自动构建并推送后端镜像至 GHCR（公开仓库免认证），作为「可部署性门禁」；DEPLOY.md 新增 §3.1「合并 ≠ 部署」部署清单（镜像重建/dist 重建/挂载前置检查/回填检查/显式 `-f` 排除 override），固化 08-22 两次部署滞后事故教训；⑤ 部署事故三连防：线上库被未合并分支的镜像迁移推进至 `20260822_001` 而 develop 缺该迁移文件，api 启动即 `Can't locate revision` 崩溃循环——#431 原样补入迁移文件修复（教训：任何 worktree 镜像直接对共享库跑 `alembic upgrade head` 都会把库推进到未合并状态，跨 worktree 部署须先确认迁移链已合入 develop）；同批 #430 修复 #421/#417 陈旧绿灯合并后的测试对账（lineage 进程缓存隔离 + dict_guard entity_type 夹具），Career Atlas #428 合并时恢复被重构挤掉的演化时间轴接线并保留 P0-1 选中清空
- **dict-guard 横向扩展：岗位/课程脏节点接入 LLM 自动清理 + 手动巡检**（#423）：契约/模型/迁移新增 `entity_type`(skill/position/course) 与 `remove_node`/`remove_edge` 动作；服务层新增岗位零引用候选、完全孤立课程、课程脏边候选生成，硬门禁对岗位/技能白名单一票否决、分级「低影响 + 高置信」删除自动生效（先写 reports/ 备份再删，低置信/高影响转人工审核池，风险不对称）；worker 每日并行三实体候选 → LLM 裁决(Pydantic 强校验) → 分级 → 按 entity_type 分派清理（删脏岗位/删孤立脏课程/删课程脏边）；admin 提案与变更审计按 entity_type 过滤与分派、新增 `POST /admin/dict-guard/trigger` 手动巡检；前端字典守卫面板对象类型展示 + 手动巡检开关；部署 JWT 密钥改挂载注入（非 COPY 进镜像）修复重建后登录 500。后端 52 单测、前端 typecheck + 6 单测通过。算法红线：岗位/课程删除判定 prompt/门禁/分级阈值需算法岗复核
- **软技能退出匹配评分 + 学习路径跳过课程匹配**（负责人拍板 2026-08-22 + 张恺天知会，随 #420 区分展示的两项遗留决策闭环）：must/nice 评分池改为纯技术栈——岗位侧软技能（`Position.soft_skills` 属性与 REQUIRES 边 `Skill.category=软技能` 条目，**must 标注也不例外**）一律进 `PositionProfile.soft_requirements` 独立通道，不参与评分与总分，仅供差距分析展示（`GapSkill.is_soft` 打标）；候选人侧 low_confidence ×0.5 降权机制保留（显式技术技能误标同样生效）。学习路径生成过滤 `is_soft` 差距（课程池为技术课，"沟通能力"命中课程属 #407 教训类语义误配），差距列表仍保留软技能条目展示。**共享岗位缓存 schema v2→v3**：读路径补 `schema_revision` 校验（此前只写不校验，旧画像会在指针命中路径被供至 7 天 TTL——本次画像 schema 变更即触发重建切换）；进程 TTL 降级路径与语义向量预热同步含独立通道。BT 黄金集回归**逐位零变化**（v1 300 对 Acc 0.9600/Spearman 0.8821、v2 384 对 Acc 0.8906/Spearman 0.7404——软技能 source_count=1 极少进 Top-10，结构性无影响）；后端全量 1739 测试通过（含通道路由/去重/差距透传/路径过滤/shared_cache 版本拒绝新用例）；设计文档 §9.2 补拍板口径
- **软技能与技术栈技能全链路区分**（契约→查询→匹配打标→前端展示）：20 项软技能白名单与 `skill_whitelist.yaml` 类目早已存在但契约零暴露、前端同色黑点不可区分。契约增字段（GraphNode/PositionSkillItem `skill_category`、PositionDetail `soft_skills`、SkillDetail `category`、GapSkill `is_soft`，全部可选向后兼容）；图谱查询 sync/async 双轨带回 `Skill.category` 与 `Position.soft_skills`（techStack Cypher 增 `s.category AS s_category`）；匹配链路 `SkillRequirement`/`GapSkill` 透传 `is_soft` **仅打标零评分变更**（BT v3 权重不动，PR 留痕张恺天知会）；`scripts/backfill_skill_category.py` 幂等回填存量节点（`kg_service` 仅 ON CREATE SET，历史节点 category 缺失，--dry-run 支持）；前端软技能粉色专属配色（#ec4899/#f472b6，与六态状态色/域紫均区分）+ 独立 category 图例开关 + 过滤面板「软技能」压暗开关（压暗不剔除）+ 岗位详情「软素质」分组 + 匹配差距「软技能」徽标 + 3D 同步；后端 334 测试（+透传单测 7 例）/前端 218 测试/mock E2E 全过。学习路径软技能缺口课程误配与匹配语义调整（软技能不计 must/独立权重）留待决策（同日拍板，见上条）
- **coursera 定向采集关键词过滤补齐**（#414）：08-14 合规修复（robots 禁 /search）改用 `/browse?query={kw}` 并注释「query 过滤」，但**过滤从未实现**且 08-22 实证 `/browse` 服务端无视 query 参数（带/不带返回同一批热门课）——定向采集 keywords=PostgreSQL/Airflow 实际把无关热门课全量入库。新增 `_keyword_hit` 本地过滤：标题/院校/技能任一命中才保留，纯 ASCII 词词边界匹配（防 air⊂hair）、CJK 子串，空关键词全量模式不变；首页零命中记日志提示改用 edx/icourse163（browse 目录是热门子集，长尾技能零命中属预期——容器内逐卡实证 40 卡=28 词不匹配+2 角色页+10 标题提取失败的历史选择器缺口，定向命中本就不可靠，补采主力仍为 edx sitemap）。误入库噪声清理：PG 2 行 + Neo4j 2 节点 DETACH DELETE（其余 24 条为既有课程 upsert 无污染）；实跑验证 keywords=PostgreSQL,Airflow 误入 **26→0**。单测 +5（15/15）
- **配置三件套目录化事故与恢复**：worktree 缺 gitignore 的 configs 三件套时 `compose up` 触发 Docker Desktop 对缺失 bind-mount 源自动建**空目录**（13:29），随后以该污染上下文构建的 worker 镜像把三个空目录烧进 `/app/configs/`（COPY configs/），单文件挂载全部 "not a directory" 崩溃；主工作区 `skill_filters_dynamic.json` 同期被建为目录致 dict-guard 动态过滤内容丢失。恢复：删目录→从 dict_change_logs 表反查历史 add_stopword 记录重建 JSON（**7 词全数找回**：Decisiveness/Futures Exchange/Pharmaceuticals/Project Files/Scientific Studies/Teaching/Textile Industry）→清上下文后重建镜像。教训：**worktree 构建镜像前必须先补齐 gitignore 配置文件，否则 COPY 会固化目录占位**；Docker 重启对此无效（镜像内容是真实目录非缓存）
- **管理端 ETL 手动触发 + 快捷操作面板三连**（数据清洗/聚合入图/完整管线）：契约先行新增 `POST /admin/etl/trigger`（白名单 job：dedup_simhash / aggregate_positions / run_etl_pipeline）与 `GET /admin/etl/task/{task_id}`（状态轮询）；worker 新增统一包装 `run_etl_job_manual`（TaskStatus 生命周期 pending→running→success/failed，阶段函数本身不追踪状态；error 固定文案防内部信息经状态端点透出，3h 超时不重试对齐主管线）；管理仪表盘快捷操作面板 6→9 项（数据清洗/聚合入图/完整 ETL 管线），触发型按钮入队即释放、3s 轮询终态更新提示；api.d.ts 契约再生成
- **审查拍板清单 #3/#5：演化模块口径统一 + 单飞与缓存**——**#3（P1-2）** 桑基图 `/skill/{id}/flow` 与 `/trends` 趋势曲线补 REQUIRES 过滤（`_requires_edges` 快照级助手，与 trend_service A-1① 同约定：有 relation 标注仅计 REQUIRES、旧快照无边标签全兼容），技能→技能边不再以「岗位」身份混入桑基列/趋势曲线；**#5（P1-3）** `_load_snapshots` 加 in-flight 单飞表（沿袭 graph.py 先例，看板并发 4-5 端点合流一次 DB 全量加载，08-15 超时教训回归修复）+ `/signals`、`/trends` 补 TTL 缓存（此前每次轮询全量重算 Z-score）
- **学习路径课程推荐治理三连：误配拦截 + 链接修复 + 无课不误导**（#405/#407/#408）：用户实证三类问题（Airflow→航空气象学、PostgreSQL→MySQL、icourse163 链接跳错误页）全链路闭环——**#405** icourse163 课程 URL 补学校简称前缀（`/course/{shortName}-{courseId}`，纯数字路径服务端 404→commonError.htm，存量 891 门全坏；`backfill_icourse163_urls.py` 从 raw_text 提取 shortName 回填 PG+Neo4j **783 条**，108 条缺 shortName 的培训类噪声课跳过）；**#407** 课程推荐跨语言无词面交集门控 `_CROSS_LANG_NO_OVERLAP_SIM=0.75`（技能名纯 ASCII × 标题含中文 × 无词面交集时 sim 需 ≥0.75 且质量分不豁免——实证误配 Airflow↔航空气象学 0.6632 超灰带上限直通、PostgreSQL↔MySQL 课 0.548；原 P1-1「PostgreSQL→MySQL 可救案例」口径经用户裁决废除，无一一致课程宁缺毋滥；单测 30/30，50 案例 A/B course 维 0.874→0.838 掉分项即目标误配）；**#408** 前端无一致课程不渲染跳转链接（时间轴「前往学习」CTA 无 url 不渲染去死按钮、节点面板空 source_url 课程卡改纯文本）；**配套补采**：edx sitemap 关键词定向（postgresql/airflow）入 6 门真实课（密歇根大学 PostgreSQL 系列×5 + IBM ETL/Airflow 管道课）并跑 load/evaluate/enrich 三阶段入图，复验 course 维回补至 **0.857**（剩余差距为 VLSI/UART/Vivado 等嵌入式技能如实无课），线上 API 实测 Airflow→仅 IBM 管道课、PostgreSQL→密歇根系列×3。coursera 定向采集同日发现返回浏览模式热门课（26 条误入库），随即 #414 修复（见下条）。算法红线留痕：门控收紧经负责人拍板（用户 08-22 指示）+ 待张恺天知会
- **爬虫 reactor 回归发现与恢复**：08-22 三课程源定时采集全崩（`RuntimeError: installed reactor epoll ≠ asyncio`）——#353 引入的模块级 `from twisted.internet import reactor` 被 SpiderLoader 预加载抢先安装默认 reactor，**#404 当日已修但未进运行镜像**（镜像构建滞后于 develop 合并）；本次以容器内热修（拷入 #404 版 middlewares/zhilian）恢复采集并完成补采，镜像重建后永久生效。**部署教训**：api 容器代码经 `docker-compose.override.yml` 以 bind mount 透传主工作区 `backend/app`——重建镜像不切换主工作区分支则改动不生效，生产化部署须无 override 重建（本次 api 已按无 override 方式运行镜像代码）；worker 无挂载、镜像代码即生效
- **岗位职能域：岗位投影 Leiden 修复社区质量**（GraphNode 契约 +domain_id/domain_name）：既有技能共现 Leiden 对岗位域不可用（min_weight 过滤后仅 7% 技能有社区，金融类岗位技能边全 nice 因子 0.2 整域滤出图外，岗位 community_id 继承只落 2-3 个技术大杂烩桶）；新增 `load_position_projection`（岗位-岗位共享技能加权投影，must 共享 1.0/nice 0.3，共享<2 噪声边过滤）+ `scripts/sync_position_domains.py`（Leiden resolution=1.55 网格搜实证 + 单点簇合并通用桶 + 最大域占比/语义域数双门禁 + Position.domain_id/domain_name 幂等回填），panorama/view 节点透出（queries 同步/异步双链路 + gen:api 同提交）。真实库回填 96 岗/14 域：金融域 19 岗（投资/精算/策略/信贷+量化/市场/商业智能聚齐，代表岗=数据分析师）、语音算法归算法域、前端/硬件/教育/运维各自成簇、桥梁岗（Python/大模型/DevOps/DBA/网络安全）合并通用桶；消费方=能力图谱域聚合下钻（后续前端 PR）
- **Z-score 占比口径归一化：评审三确认项闭环**：第五轮审查算法条目 A-1/A-2/A-3 经负责人拍板（①方案）随分支落地——**A-1①** 演化信号分子与分母同边集（均仅 REQUIRES，BELONGS_TO 等技能→技能边不再混入分子，占比可>1 问题消除，与 state_machine 过滤约定对齐；旧快照无边标签按历史口径兼容）；**A-2①** Z-score 序列整列同口径（全部窗口有占比分母才用占比，任一窗口缺分母整序列退回计数——堵住新旧口径混排致批量伪 declining 复活）；**A-3①** 检测侧消费 `GraphVersion.data_warning`（证据量萎缩<50%/膨胀>200% 的快照整期剔除，不作为 current 也不进 μ/σ，堵部分源故障反向伪 emerging；展示侧打标不剔除不变）。附三个评审指出的测试缺口回归（混合口径序列/部分源故障反向/非 REQUIRES 分子）；设计文档 §7.1、openapi EvolutionSignal 描述同步口径（SSOT），前端类型再生成。幻觉防控/演化算法红线留痕：负责人拍板 + 张恺天知会
- **第五轮全项目代码审查立即修批次**：基于《全项目代码审查报告_20260822.md》（develop 8f0f3d6 增量 +10,256 行，总评 B+）拍板清单 #1/#2 三处 P1——**P1-1** dict-guard 动态过滤跨容器断链修复（compose api/worker 补 `skill_filters_dynamic.json` 单文件挂载；`dynamic_filters._write` 弃 tmp+os.replace 改直接覆写——单文件 bind mount 上 rename 覆盖挂载点 EBUSY，与 runtime_settings 同口径，损坏由 `_load` 空层兜底；DEPLOY.md 补宿主空层文件预建引导）；**P1-6** `compute_confidence` 下界钳制（全零输入+孤立技能曾产出 −0.075 被 Schema `ge=0.0` 拒绝崩 discovery worker，实证路径）；**P1-7** `thresholds._get` 坏值按键回退默认（null/非数字单键曾致 int()/float() 裸抛 TypeError 停摆 SimHash/时滞检测全链，实证路径）。连带修 **dict_guard_gate 非确定性取样**（`next(iter(SKILL_STOPWORDS))` 随 PYTHONHASHSEED 抽中「微」/白名单重叠词致 CI 偶发红，改排序筛选纯停用词）。幻觉防控域改动按红线留痕：负责人拍板 + 张恺天知会
- **第五轮全项目代码审查报告落档**（#402）：docs/reviews/ 全项目代码审查报告_20260822.md，总评 B+/P1×7/Z-score 分支三条必须确认待张恺天裁决/拍板清单 12 项
- **匹配引擎退化场景修复：无 must 白送分 + nice 长尾稀释**（A1+A3+B1，负责人拍板直施、算法红线留痕待张恺天复核）：实测 43 个在营岗位（freq≥3 非 legacy）中 **7 个（16.3%）零 must**（`_is_must` 三重条件对金融/分析类 JD 标注稀疏一条不过），旧口径 `must_total_weight==0 → 1.0` 使其对任意候选人保底送 w_must×1.0（v3 权重=总分 66.9%），前端开发候选的推荐 Top-10 被 7 个零 must 岗位占据且永越 0.5 匹配阈值；nice 池为聚合层全量倾倒（中位 36 / p90 271 / max 348），全量 Σ/Σ 占比使资深前端命中 30/348 仅得 0.012。修复三件套——**A1** 无 must 时 `must_score=None`（契约可空、雷达维度不展示、总分 `(nice×w_nice+exp×w_exp)/(w_nice+w_exp)` 重归一）；**A3** 无门槛岗位加分技能全未命中同样判零（防无关候选占位）；**B1** nice 改 Top-K=10 覆盖率（跨源数降序，nice 边权重统一 0.4 无区分度故用 source_count 排序），分数反映"最想要的前 10 个加分项满足几成"。真实图谱前后对比（纯规则口径）：前端开发候选 Top-10 零 must 岗位 **7→1**、前端开发工程师 nice 0.012→0.160、真前端岗（Vue/React/前端开发）全部回到榜单头部；无关候选对零 must 岗位由 0.956 分落榜变 unqualified。BT 黄金集回归**逐位零变化**（v1 300 对 Acc 0.9600/Spearman 0.8821、v2 384 对 Acc 0.8906/Spearman 0.7404——黄金集构造保证 must 非空且 nice≤1，结构性不受影响）；诊断 prompt must 可空防崩 + "无门槛"文案；前端 must=null 雷达剔维/条形图空条/数值"—"；后端 138+32 测试、前端 202 测试与 build 通过。**追加固件（O1，同日拍板）**：无门槛岗位判零门从「nice 全未命中」收紧为「nice Top-K 命中率 <20%」（前 10 条核心加分项命中不足 2 条不推荐）——观察项实证 freq=3 小样本岗（AI基础设施工程师，req_years 缺失、nice 池单源噪声 sc≤2）前端候选仅命中 1 条 TypeScript 即 0.88 倒挂真前端岗 0.63-0.70，收紧后该岗对前端/后端候选均落榜，金融候选对精算（0.30）/策略（0.20 边界）保留；B1 补确定性：source_count 平局按 skill_name 升序截断（小样本岗 sc 全为 1 时 Top-10 组成不随图谱返回序漂移）

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
