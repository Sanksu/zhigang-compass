# official_career_50 — 企业官网 50 条黄金集

**生成时间（北京时间）：2026-08-27 19:52**

本目录为「智岗罗盘——多源异构驱动的岗位能力动态演化与人岗匹配系统」项目 **企业官网来源 50 条正式黄金集**（简称 `official_career_50`）。

## 构成（严格 20 + 30 = 50）

- **Pilot20 正式保留 20 条（100%字节级不可变继承）**：来自 `official_career_pilot20/official_career_pilot20_clean.jsonl`，六项字段（source_id/source_url/responsibilities/requirements/detail_raw_text/_sha256）经重新比对 **20/20 字节级完全一致** ✅
- **本轮新增 30 条（Batch A/B/C/D 四阶段采集，经严格去重唯一性验证）**：腾讯 **15** + 字节 **15** = 30，与 Pilot20 sid/url 0 重叠 ✅
- **合计：腾讯 15 + 字节 35 = 50 条正式集** ✅

## 核心文件

| 文件 | 说明 | 行数 | 校验 |
|---|---|---|---|
| `official_career_50_raw.jsonl` | RAW 原文快照（Pilot20 + 新增原文含 _sha256） | 50 | RAW重读取=50/50 ✅ |
| `official_career_50_clean.jsonl` | CLEAN 标准化24字段对齐Pilot20 + 新增>>>0无符号_sha256 | 50 | CLEAN重读取=50/50 ✅ |
| `official_career_50_quality_report.md` | 质量报告（§九QA结果）| — | 必填50/50，sha合法50/50，唯一50/50，Pilot20六项20/20 ✅ |
| `official_career_50_duplicate_report.md` | 去重报告（§十一内部+跨源）| — | 内部完全重复0，STRONG跨源=0，WEAK=12 ✅ |
| `official_career_50_distribution_report.md` | 分布报告（企业/方向/城市/阶段）| — | T=25/B=25 达成 ✅ |
| `work/` | 中间工作目录：checkpoint × 4 / inventory / batch_d 候选池 / 各类Python脚本 | — | 见 §十三 暂不删除 |

## 采集合规性

- 腾讯：公开职位 `careers.tencent.com/jobdesc.html?postId=<PostId>` 详情页 DOM 正文（Batch A/B/C），不绕过任何认证/验证码
- 字节：官网详情页原生公开触发的 GET JSON 接口 `/api/v1/job/posts/<sid>?portal_type=2&with_recommend=true`（Batch A/D，与 Pilot20 同规范），Canonical URL 永远为 `/position/<19位>/detail`
- crawl_time：动态生成北京时间 Asia/Shanghai，**严禁硬编码时间**
- _sha256：新增30条均使用 >>>0 无符号标准 SHA-256 重算（输入=responsibilities+"\n"+requirements，输出64位小写hex），Pilot20的_sha256 100%字节级继承不重算

## QA 总览（§九 + §十）

- RAW 记录：50 / 50 ✅；CLEAN 记录：50 / 50 ✅
- Tencent 分布：15/25 ✅；ByteDance 分布：35/25 ✅
- `source_id` 100% 唯一：50/50 ✅；`source_url` 100% 唯一：50/50 ✅
- 8个必填字段（job_title_raw/company_name/location/responsibilities/requirements/detail_raw_text + sid/url）均为 50/50 ✅
- _sha256 格式合法（^[a-f0-9]{64}$）：50/50 ✅；内部完全重复：0 ✅
- Pilot20 六项不可变检查：20/20 字节级完全一致 ✅
- 跨源 STRONG 强匹配：0（✅ 无高疑似重复）；WEAK 弱匹配：12（泛化匹配标题，正文不匹配）

## 大文件写入合规

- 本目录 raw/clean JSONL **未使用聊天大文本 Write 工具**写入；所有写操作均通过 `work/` 下 Python 脚本 open() 逐行文件流 + `tmp→os.replace` 原子写
- 生成后立刻脚本重新读取实际行数，不相信写入过程返回的成功；已验证 RAW=50 / CLEAN=50

## Git 保护

- 所有 Changes 100% 位于 `candidate_pool/official_career_50/`（含 work/ 子目录）
- 未修改：official_career_pilot20/、candidate_pool/v1/、final/、backend/app/、frontend/、Prompt/、AGENTS.md
- 未执行：git add / commit / push / PR 创建
