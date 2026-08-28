# unified_jd_audit — JD 三数据集统一只读审计目录

> 生成产物专用目录，所有文件均为只读派生结果。

本目录为 **「JD 三数据集统一审计：Zhilian Candidate + Gold110 + Official Career50」** 阶段产物。
**不回写任何文件到保护区**：
- 不写回 `candidate_pool/v1/`（Zhilian）
- 不写回 `candidate_pool/official_career_50/`（Official Career 封板数据）
- 不写回 `final/`（Gold 110 正式黄金集）
- 不修改 `backend/app/`、`frontend/`、`Prompt/`、`AGENTS.md`

## 输入数据位置（只读）

| 名称 | 路径 |
|---|---|
| Zhilian Candidate（clean） | `backend/data/golden_set/candidate_pool/v1/real_jd_candidates_clean.jsonl` |
| Gold 110（正式） | `backend/data/golden_set/final/jd_golden_110.jsonl` |
| Official Career 50（clean，封板） | `backend/data/golden_set/candidate_pool/official_career_50/official_career_50_clean.jsonl` |

## 脚本（可复现）

`scripts/run_unified_audit.py` — 纯 Python stdlib 脚本，不引入任何额外依赖。

执行：
```
cd backend/data/golden_set/audit/unified_jd_audit
python scripts/run_unified_audit.py
```

脚本行为：
1. 只读加载三块 JSONL
2. 派生临时字段（`__nfam` / `__ncomp` / `__ntitle` / `__nloc`）用于分类与匹配，**不写回原文件**
3. 输出本目录下 6 个 CSV + 1 个汇总 MD

## 产物清单

| 文件 | 内容 |
|---|---|
| `unified_jd_audit_summary.md` | 汇总报告（§十四所有核心问题回答，供人工审阅） |
| `unified_jd_field_coverage.csv` | 字段存在率 / 非空率 / 类型 Top3 / 异常 |
| `unified_jd_job_family_distribution.csv` | 13 族岗位族分布（逐数据集） |
| `unified_jd_cross_source_duplicates.csv` | Zhilian Candidate × Official 跨源重复（EXACT / STRONG / WEAK） |
| `unified_jd_gold_overlap.csv` | Gold × Zhilian / Gold × Official 重合明细（泄漏风险审计） |
| `unified_jd_quality_issues.csv` | 字段边界 / SHA / 时间 异常逐行列表 |
| `README.md` | 本文件 |

## Git 保护

本目录 `backend/data/golden_set/audit/unified_jd_audit/` 下所有文件应 100% 为新增产物，不应出现任何
其他路径的改动。提交前请使用 `git diff --name-only` 确认。

## 补充说明（08-28 审查后记）

### gold_eval_exclusion_manifest.csv 的性质

该清单是**人工参照物，无自动化消费方**：`tests/evaluate/run_manual_jd_eval.py` 与 `scripts/evaluate.py`
均不读取它，评测也不会自动跳过清单内样本。当前功能上无需自动排除——六维评测输入为
`detail_raw_text`（全文），resp/req 切分不进入评测；清单内 ANN-0023 已人工确认 GOLD_CORRECT
（KEEP_GOLD / SOURCE_EVIDENCE_NOTE_ONLY）。用途限定为：人工解读评测结果、追溯 gold 父子血缘时
的对照表。

### candidate_pool/v1/batch_embedded_security.json

来历：42bb4ee（08-17 候选池+标注包）附带的安全域候选嵌入批次产物，**无代码消费方**。
其 `_sha256` 原为顺序占位假值（24/26 条），08-28 已按统一公式 `SHA256(resp+\n+req)` 补为真实
计算值；两条修复记录（CC1487/CC4042）的哈希已与 clean.jsonl / clean.csv / manifest 交叉对齐。

### 已知遗留（不阻塞 BASELINE_READY）

- `split_responsibilities_requirements` 启发式切分器未改，同类职责/要求误切分仍可能在新采集
  中出现，依赖本审计门控兜底；采集侧 sha 公式与输出路径已在 PR #624 对齐。
