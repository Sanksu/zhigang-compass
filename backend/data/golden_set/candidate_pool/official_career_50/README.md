# official_career_50 — 企业官网 50 条正式候选数据集

**生成时间（北京时间）：2026-08-28 15:11**

本目录为「智岗罗盘——多源异构驱动的岗位能力动态演化与人岗匹配系统」项目 **企业官网 50 条正式候选数据集**（简称 `official_career_50`）。

> 📌 **目录定位**：当前属于 `backend/data/golden_set/candidate_pool/official_career_50/`（candidate_pool 阶段），**尚未进入 official Gold 110 条正式黄金集**。本 50 条用于多来源 JD 采集管线、清洗、去重和后续评测流程的对照基准（可扩展迭代）。

## 构成（严格 Pilot20 + 本轮新增 30 = 50）

- **Pilot20 正式保留 20 条（100%字节级不可变继承）**：来自 `../official_career_pilot20/official_career_pilot20_clean.jsonl`，六项字段（source_id / source_url / responsibilities / requirements / detail_raw_text / _sha256）经重新比对 **20/20 字节级完全一致** ✅
  - Pilot20 组成：Tencent **10** + ByteDance **10** = 20
- **本轮新增 30 条（经严格去重唯一性验证，与 Pilot20 sid/url 0 重叠）**：Tencent **15** + ByteDance **15** = 30 ✅
- **最终合计：Tencent 25 + ByteDance 25 = 50 条正式候选数据集** ✅

## 核心文件

| 文件 | 说明 | 行数 | 校验 |
|---|---|---|---|
| `official_career_50_raw.jsonl` | RAW 原文快照（Pilot20 + 新增原文含 _sha256） | 50 | RAW重读取=50/50 ✅ |
| `official_career_50_clean.jsonl` | CLEAN 标准化24字段对齐Pilot20 + 新增>>>0无符号_sha256 | 50 | CLEAN重读取=50/50 ✅ |
| `official_career_50_quality_report.md` | 质量报告（含publish_time未来异常专项标注）| — | 必填50/50，sha合法50/50，唯一50/50，Pilot20六项20/20 ✅ |
| `official_career_50_duplicate_report.md` | 去重报告（内部8对近似+跨源STRONG=0/WEAK=12）| — | L24/25 + L27/48 两组人工复核 DISTINCT_JOBS ✅ |
| `official_career_50_distribution_report.md` | 分布报告（企业/方向/城市/阶段）| — | T=25/B=25 达成 ✅ |
| `work/` | 复现/QA脚本目录 | — | 仅保留 6 个正式脚本；详见本 README §「复现与QA脚本（work/）」 |

## 采集合规性

- Tencent：公开职位 `careers.tencent.com/jobdesc.html?postId=<PostId>` 详情页 DOM 正文（Batch A/B/C），不绕过任何认证/验证码
- ByteDance：官网详情页原生公开触发的 GET JSON 接口 `/api/v1/job/posts/<sid>?portal_type=2&with_recommend=true`（Batch A/D，与 Pilot20 同规范），Canonical URL 永远为 `/position/<19位>/detail`
- crawl_time：动态生成北京时间 Asia/Shanghai，**严禁硬编码时间**
- _sha256：新增30条均使用 >>>0 无符号标准 SHA-256 重算（输入=responsibilities+"\n"+requirements，输出64位小写hex），Pilot20的_sha256 100%字节级继承不重算
- source_company（canonical 元字段）：仅允许两个英文值 `Tencent` / `ByteDance`；`company_name` 继续保留真实公司全称（如 腾讯科技（深圳）有限公司 / 北京字节跳动网络技术有限公司），两者不可混淆

## QA 总览（§九 + §十 + §七 防回归）

- RAW 记录：50 / 50 ✅；CLEAN 记录：50 / 50 ✅
- Tencent 分布：25/25 ✅；ByteDance 分布：25/25 ✅
- Pilot20 分段：Tencent=10/10、ByteDance=10/10 ✅；本轮新增 30 分段：Tencent=15/15、ByteDance=15/15 ✅
- `source_id` 100% 唯一：50/50 ✅；`source_url` 100% 唯一：50/50 ✅
- 9 个必填字段（job_title_raw / company_name / location / responsibilities / requirements / detail_raw_text / source_id / source_url / _sha256）均为 50/50 ✅
- _sha256 格式合法（^[a-f0-9]{64}$）：50/50 ✅；内部完全重复：0 ✅
- Pilot20 六项不可变检查：20/20 字节级完全一致 ✅
- publish_time 未来异常：**2 条**（已在 quality_report 显式标注「source-reported future publish_time anomaly」，不猜不改不交换月日）
- 跨源 STRONG 强匹配：0（✅ 无高疑似重复）；WEAK 弱匹配：12（标题泛化匹配，正文不匹配）

## 复现与 QA 脚本（work/）

本目录下 `work/` **仅保留 6 个可复现/QA 正式脚本**（2026-08-28 本轮清理后，checkpoint × 4 / inventory / batch_d 候选池等临时存档已全部移除不保留）：

| 脚本 | 作用 | 触发 |
|---|---|---|
| `work/build_official_50.py` | 从 Pilot20 clean +（checkpoint 或 OFFICIAL_RAW fallback）生成 raw/clean = 50；source_company canonical；新增 sha >>>0 | 需要重生成 raw/clean 时运行 |
| `work/qa_official_50.py` | 正式 QA：raw=50 / clean=50 / T25/B25 / 唯一50/50 / sha50/50 / Pilot20六项20/20 / **§七 防回归七条** | 每次重生成后必跑 |
| `work/cross_source_duplicates.py` | 官网50 × (智联v1 + Gold 110) 三重跨源去重，STRONG/WEAK 分级 | 跨源复核或 Gold/智联有更新时运行 |
| `work/regen_4_reports.py` | 从 50 条 + QA 存档 重新生成 4 份正式 MD（README / quality / duplicate / distribution） | 每次 build + qa 后必跑 |
| `work/precommit_final_audit.py` | 提交前终审：重复近似复核 + 阶段组成 + Pilot20 保护 + Git 范围 100% 在 official_career_50/ | commit 前必跑 |
| `work/rebuild_dataset.py` | checkpoint 解析异常 / 伪JSONL / 字面量 \\n 拆分等重建数据集工具 | 工作目录历史数据损坏时的恢复工具 |

## 大文件写入合规

- 本目录 raw/clean JSONL **未使用聊天大文本 Write 工具**写入；所有写操作均通过 `work/` 下 Python 脚本 open() 逐行文件流 + `tmp→os.replace` 原子写
- 生成后立刻脚本重新读取实际行数，不相信写入过程返回的成功；已验证 RAW=50 / CLEAN=50

## Git 保护

- 所有 Changes 100% 位于 `candidate_pool/official_career_50/`（含 work/ 子目录）
- 未修改：official_career_pilot20/、candidate_pool/v1/、final/（Gold 110）、backend/app/、frontend/、Prompt/、AGENTS.md
