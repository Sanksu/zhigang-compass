# Unified JD 三数据集审计汇总（只读）

生成时间：2026-08-28 22:29（本地机器时间）

审计范围：
- Zhilian Candidate：real_jd_candidates_clean.jsonl
- Gold 110：jd_golden_110.jsonl
- Official Career 50：official_career_50_clean.jsonl

所有产物位于：backend/data/golden_set/audit/unified_jd_audit/

## 1. 数据规模（§四，现场程序重读取）

| 数据集 | 记录数 |
|---|---|
| Zhilian Candidate | 158 |
| Gold 110 | 110 |
| Official Career 50 | 50 |

## 2. 来源与构成（§六）

**Zhilian Candidate 来源构成 Top（字段 source）**：
zhilian:158

Zhilian distinct companies：140

**Official Career 50 构成**：Tencent = 25，ByteDance = 25（预期 25/25：✅ 符合）

**Gold 来源构成 Top（字段 source）**：
zhilian:110

Gold distinct companies：103

Gold 预期规模 110：✅ 符合

## 3. 字段完整性（§五 摘要，详见 unified_jd_field_coverage.csv）

逐数据集必填 9 字段（job_title_raw / company_name / location / responsibilities / requirements / detail_raw_text / source_id / source_url / _sha256）完整情况：

| 数据集 | 完整率最差字段 | 最差非空率 |
|---|---|---|
| Zhilian | N/A | 100.0% |
| Gold | _sha256 | 0.0% |
| Official | N/A | 100.0% |

## 4. 岗位族覆盖（§七，详见 unified_jd_job_family_distribution.csv）

13 族统一只读分类（不修改 job_title_raw）。

### 各数据集 Top3 岗位族

| 数据集 | #1 | #2 | #3 | 其他族占比 |
|---|---|---|---|---|
| Zhilian | 其他 27(17%) | 后端 22(14%) | 全栈 19(12%) | 57% |
| Gold | 其他 17(15%) | 后端 17(15%) | 全栈 13(12%) | 57% |
| Official | 其他 18(36%) | 后端 9(18%) | AI/LLM 8(16%) | 30% |

**过度集中（单一族≥40%）**：无

**缺口族（记录0条，不含"其他"）**：Zhilian/客户端, Gold/客户端, Official/数据分析, Official/嵌入式/C++, Official/网络/安全

## 5. 跨源重复（§八 Zhilian Candidate × Official Career 50）

详见 unified_jd_cross_source_duplicates.csv。

- EXACT_DUPLICATE：0
- STRONG_SUSPECT：0
- WEAK_SUSPECT：0

（说明：EXACT = source_id / source_url / _sha256 exact；STRONG = normalized company+title+location 三者完全一致；WEAK = 同公司+正文相似度≥0.80。级别互斥取最高级，禁止自动删除）

## 6. Gold 重合 / 泄漏风险（§九）

详见 unified_jd_gold_overlap.csv。

**Gold × Zhilian Candidate**
- EXACT_GOLD_OVERLAP：110
- STRONG_GOLD_OVERLAP：0
- WEAK_GOLD_OVERLAP：0

**Gold × Official Career 50**
- EXACT_GOLD_OVERLAP：0
- STRONG_GOLD_OVERLAP：0
- WEAK_GOLD_OVERLAP：0

> 说明：Gold 本来就是从 candidate 流水线人工标注抽取而来，重合不代表错误；重点关注重合条目在后续 train/eval 切分中是否可能造成 leakage，本阶段仅审计、不删除。

## 7. 字段边界异常（§十 + quality issues CSV 摘要）

| 数据集 | 异常条数 | 主要异常类型 Top |
|---|---|---|
| Zhilian | 1 | resp_req_high_overlap_sim0.90:1 |
| Gold | 2 | requirements_EMPTY:1, detail_pure_duplicate_of_resp:1 |
| Official | 16 | future_publish_time:2, detail_shorter_than_resp_req_both_len(371vs648):1, detail_shorter_than_resp_req_both_len(339vs625):1 |

（详见 unified_jd_quality_issues.csv）

## 8. SHA-256 公式一致性（§十一）

| 数据集 | 总记录 | _sha256 存在 | 64hex 格式合法 | SHA(resp+"\\n"+req) 公式一致 | 公式不一致 | 缺失 | 备注 |
|---|---|---|---|---|---|---|---|
| Zhilian Candidate | 158 | 158 | 158 | 158 | 0 | 0 |  |
| Official Career 50 | 50 | 50 | 50 | 50 | 0 | 0 | 预期 100% 一致 |
| Gold 110 | 110 | 0 | 0 | — | — | — | Gold口径不同，不重算SHA公式；仅报告存在率与格式 |

## 9. 时间字段审计（§十二）

| 数据集 | publish_time 空 | crawl_time 空 | publish > crawl（未来日期） | 解析错误 | 备注 |
|---|---|---|---|---|---|
| Zhilian Candidate | 144 | 0 | 0 | 0 |  |
| Gold 110 | 110 | 110 | 0 | 0 |  |
| Official Career 50 | 25 | 0 | 2 | 0 | 已知 future=2，本轮确认：✅ True |

## 10. 数据边界结论（§十四回答的核心）

- **可继续作为 candidate pool**：
  - Zhilian Candidate（real_jd_candidates_clean.jsonl）：字段完整度在上述最差行显示，若 SHA 公式不一致条数在 quality CSV 中已逐条列出，建议人工复核不一致来源后再进入下一阶段
  - Official Career 50：T25/B25 达成 ✅，Pilot20 六字段 20/20 PASS ✅，SHA 100% 公式一致预期应已达成，future anomaly 2 条按规定保留 ✅ → 作为 candidate pool 正式候选已封板 ✅
- **正式 Gold**：jd_golden_110.jsonl（110 条）= 已进入 final/ 的人工标注黄金集，独立于 candidate pool
- **需要人工复核记录**：
  - quality issues CSV 所有行（字段空/职责要求异常/SHA 异常/时间异常）
  - cross-source duplicates CSV 全部 Exact 与 Strong 行：是否真正重复岗位或 DISTINCT_JOBS（不同城市/PostId）需人工判定
  - gold_overlap CSV 全部重合行：是否可能造成评测时 train/eval leakage，需在切分时按 source_id 黑名单防泄漏
- **泄漏风险等级**：
  - Gold × Zhilian / Gold × Official 的重合条目如果为 EXACT / STRONG，且同一岗位同时出现在评测集，则构成 data leakage → 建议评测 pipeline 在加载 Gold eval split 前对 candidate train split 按 source_id + source_company 黑名单剔除；WEAK 级仅提示，不强制

## 11. 产出物清单

1. `unified_jd_audit_summary.md` — 本文件
2. `unified_jd_field_coverage.csv` — 三数据集逐字段 存在率/非空率/类型/异常
3. `unified_jd_job_family_distribution.csv` — 13 族岗位族 逐数据集 数量/占比
4. `unified_jd_cross_source_duplicates.csv` — Zhilian × Official 跨源重复 四层级明细
5. `unified_jd_gold_overlap.csv` — Gold × Zhilian / Gold × Official 重合明细
6. `unified_jd_quality_issues.csv` — 字段边界/SHA/时间 异常逐行列表
7. `README.md` — 审计目录说明
8. `scripts/run_unified_audit.py` — 只读审计脚本（可复现）
