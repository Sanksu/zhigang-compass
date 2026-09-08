# 数据模块最终封板审计报告（Final Data Seal）

## 【审计基线】
- 分支：`feature/data-final-seal`（feature/data-llm-baseline-110 保留未删）
- audit_base_sha：`0c111e97d26af7030a7fb9244615b8d5b18367d3`（= 最新 origin/develop；本地 HEAD 经 `git merge --ff-only` 快进至此，**无 merge commit**）
- 上轮基线：`c44504303aaa176d1a5d0251c30f7b0b7a27c8c3`（09-08 首次封板基线；本轮 `git -c http.version=HTTP/1.1 fetch` 恢复后 ff-only 刷新）
- 审计时间（北京时间）：2026-09-08 15:20 UTC+08:00
- 审计性质：**只读**；不调用 LLM、不自动修复、不修改任何数据字节

## 【数据规模】CURRENT_DATA_COUNTS
- Zhilian raw：**158**
- Zhilian clean：**158**（tier: accepted=135, review_required=23）
- review_required.csv 数据行：**23**
- rejected.csv 数据行：**0**
- Gold110：**110**
- Official Career raw/clean/gold：**50 / 50 / 50**
- Gold exclusion manifest 行：**110**
- 统一审计 cross_source_duplicates.csv 行：0

## 【唯一性】
**Zhilian clean (158)**

| 字段 | total | non_empty | unique | 重复值数 |
|---|---|---|---|---|
| source_id | 158 | 158 | 158 | 0 |
| source_url | 158 | 158 | 158 | 0 |
| _sha256 | 158 | 158 | 158 | 0 |

**Gold110**

| 字段 | total | non_empty | unique | 重复值数 |
|---|---|---|---|---|
| sample_id | 110 | 110 | 110 | 0 |
| source_id | 110 | 110 | 110 | 0 |
| source_url | 110 | 110 | 110 | 0 |

**Official Career50 clean**

| 字段 | total | non_empty | unique | 重复值数 |
|---|---|---|---|---|
| source_id | 50 | 50 | 50 | 0 |
| source_url | 50 | 50 | 50 | 0 |
| _sha256 | 50 | 50 | 50 | 0 |

**Official Career50 gold**

| 字段 | total | non_empty | unique | 重复值数 |
|---|---|---|---|---|
| sample_id | 50 | 50 | 50 | 0 |
| source_id | 50 | 50 | 50 | 0 |
| source_url | 50 | 50 | 50 | 0 |

**Exclusion manifest**

| 字段 | total | non_empty | unique | 重复值数 |
|---|---|---|---|---|
| source_id | 110 | 110 | 110 | 0 |
| gold_source_id | 110 | 110 | 110 | 0 |

## 【字段完整率】
**Zhilian clean**

| 字段(逻辑) | 实际key | non_empty | empty | 覆盖率 |
|---|---|---|---|---|
| title | job_title_raw | 158 | 0 | 100.0% |
| company | company_name | 158 | 0 | 100.0% |
| location | location | 158 | 0 | 100.0% |
| responsibilities | responsibilities | 158 | 0 | 100.0% |
| requirements | requirements | 158 | 0 | 100.0% |
| detail_raw_text | detail_raw_text | 158 | 0 | 100.0% |
| source_id | source_id | 158 | 0 | 100.0% |
| source_url | source_url | 158 | 0 | 100.0% |

**Gold110（源字段）**

| 字段(逻辑) | 实际key | non_empty | empty | 覆盖率 |
|---|---|---|---|---|
| title | job_title_raw | 110 | 0 | 100.0% |
| company | company_name | 110 | 0 | 100.0% |
| location | location | 110 | 0 | 100.0% |
| responsibilities | responsibilities | 110 | 0 | 100.0% |
| requirements | requirements | 109 | 1 | 99.09% |
| detail_raw_text | detail_raw_text | 110 | 0 | 100.0% |
| source_id | source_id | 110 | 0 | 100.0% |
| source_url | source_url | 110 | 0 | 100.0% |

**Official Career50 clean**

| 字段(逻辑) | 实际key | non_empty | empty | 覆盖率 |
|---|---|---|---|---|
| title | job_title_raw | 50 | 0 | 100.0% |
| company | company_name | 50 | 0 | 100.0% |
| location | location | 50 | 0 | 100.0% |
| responsibilities | responsibilities | 50 | 0 | 100.0% |
| requirements | requirements | 50 | 0 | 100.0% |
| detail_raw_text | detail_raw_text | 50 | 0 | 100.0% |
| source_id | source_id | 50 | 0 | 100.0% |
| source_url | source_url | 50 | 0 | 100.0% |

**Gold110 标注字段（gold_*）完整率**

| 字段 | non_empty | empty | 覆盖率 |
|---|---|---|---|
| gold_title | 110 | 0 | 100.0% |
| gold_skills | 110 | 0 | 100.0% |
| gold_bonus_skills | 46 | 64 | 41.82% |
| gold_experience | 108 | 2 | 98.18% |
| gold_education | 109 | 1 | 99.09% |
| gold_core_duties | 110 | 0 | 100.0% |

## 【SHA一致性】Zhilian candidate pool
- 公式（仓库验收同口径）：`SHA256(responsibilities + "\n" + requirements)  [与 audit/unified_jd_audit 验收脚本同口径]`
- total：**158**；match：**158**；mismatch：**0**；格式非法：0；resp-only legacy：0

## 【两条历史修复专项回归】
- `CC148739350J40212149403`（ANN-0023）：present=True，resp_non_empty=True（len=113），req_non_empty=True（len=112），sha_match=True
- `CC404298980J40856902010`：present=True，resp_non_empty=True（len=41），req_non_empty=True（len=117），sha_match=True

## 【Gold保护】
- Gold SHA256：`ceedfa6987fee665ea53f17678e8f06cb197a632bc828f99c5b962615c508061`
- Gold size：391398 bytes
- Gold records：**110**（==110：True）
- sample_id 唯一：True
- 与历史封板基线 SHA（ceedfa6987fee665…）一致：**True**
- Gold 修改：**0 byte**（审计全程只读）
- 6 个 gold_* 字段齐备：{'gold_title': True, 'gold_skills': True, 'gold_bonus_skills': True, 'gold_experience': True, 'gold_education': True, 'gold_core_duties': True}

## 【ANN-0023 专项回归】
- Gold 记录存在：True；source_id=CC148739350J40212149403（==T1：True）
- 结论：**GOLD_CORRECT**（final_acceptance_summary.json ANN0023_correct=true；无新仓库证据推翻旧结论，保持 GOLD_CORRECT（不因 source evidence 边界改 Gold））

## 【Gold exclusion】
- manifest 行：**110**；source_id 非空/唯一：110/110
- gold_source_id 覆盖：**110/110**（Gold 110 条 source_id 全部在 manifest：True）
- ANN-0023 / CC148739350J40212149403 在排除关系中：True / True
- EXPECTED_PARENT_CHILD_OVERLAP 标注行：110
- 数据级结论：110 条 Gold 来源候选具备明确排除映射，可被排除于 train/dev/retrieval exemplar/few-shot context（模型代码侧不在本审计范围）

## 【跨源关系】
- Gold vs Zhilian source_id 重叠：**110**、source_url 重叠：110 → **EXPECTED_PARENT_CHILD_OVERLAP（Gold 由 Zhilian 候选派生+人工标注，110 条父子重叠为设计预期，非 DATA_LEAKAGE）**
- Zhilian vs Official Career：source_id 重叠=0，source_url 重叠=0，_sha256 重叠=0
- Gold vs Official Career：source_id 重叠=0，source_url 重叠=0
- 统一审计 cross_source_duplicates.csv 行数=0，关系统计：{}
- 结论：除 Gold↔Zhilian 设计内父子重叠外，**无跨源 exact duplicate**（source_id/source_url/_sha256 三口径均为 0）

## 【Official Career50 专项】
- 总数：**50**；腾讯（company_name 口径）：**25**；字节/抖音：**25**
- source_company 字段分布：{"ByteDance": 25, "Tencent": 25}
- source_id 唯一：True；source_url 唯一：True；_sha256 全为合法 64-hex：True
- future publish_time 异常：**2** 条（SOURCE_REPORTED_FUTURE_TIME，保留 source 原值不修正）：
  - 7663333383728597301（北京字节跳动网络技术有限公司）publish_time=2026-11-15T00:00:00.000Z > crawl_time=2026-08-26T14:05:00.000Z
  - 7660694249809692981（北京字节跳动网络技术有限公司）publish_time=2026-11-08T00:00:00.000Z > crawl_time=2026-08-26T14:06:00.000Z

## 【统一审计状态】（读自 final_acceptance_summary.json，当前实际值）
- P0 = **0**；P1 = **0**；P2 = 2；P3 = 17
- BASELINE = **BASELINE_READY**；SHA_mismatch_count = 0；Manifest_rows = 110

## 【安全扫描】
- 扫描数据文件数：39；疑似 secret：**0**
- 明细见 final_data_security_scan.md（全部脱敏，未打印任何 secret 正文）

## 【历史基线差异】HISTORICAL_BASELINE_DIFFERENCE
- 无差异：Zhilian=158 / accepted=135 / review_required=23 / rejected=0 / Gold=110 / Official=50 / 腾讯=25 / 字节=25 / manifest=110，全部与历史基线一致。

## 【Post-baseline Golden-set Changes】
- 区间：`c44504303aaa..0c111e97d26a`（`git diff --name-status -- backend/data/golden_set/` 实测）
- 分类口径：CORE_DATA_ASSET（核心四类封板资产）/ ALGORITHM_EVAL_ASSET（算法调参评测集）/ TEST_EVAL_ASSET（测试评测集）/ OTHER_GOLDEN_SET_ASSET（其它历史留痕）
- **核心四类资产（candidate_pool/v1、final/jd_golden_110.jsonl、official_career_50、audit/unified_jd_audit）在该区间零变更。**

| 文件 | 变化 | records | 分类 | 用途 | 属核心四类 | 影响Gold110 | 影响Zhilian158 | 影响Official50 | 影响unified audit | 影响本次Seal结论 |
|---|---|---|---|---|---|---|---|---|---|---|
| backend/data/golden_set/education_semantics_v1.jsonl | A 新增 | 82 | ALGORITHM_EVAL_ASSET | 学历语义聚合评测集（scripts/generate_education_semantics.py 生成；匹配/聚合算法调参与测试使用） | 否 | 否 | 否 | 否 | 否 | 否 |
| backend/data/golden_set/golden_set_match_v3.jsonl | A 新增 | 384 | ALGORITHM_EVAL_ASSET | 人岗匹配 v3 评测集（scripts/build_match_golden_v3.py 构建；backend/tests/matching 使用） | 否 | 否 | 否 | 否 | 否 | 否 |
| backend/data/golden_set/position_domain_eval.jsonl | M 修改 | 73 | TEST_EVAL_ASSET | 岗位领域图谱评测集（backend/tests/graph/test_position_domain.py 使用） | 否 | 否 | 否 | 否 | 否 | 否 |
| backend/data/golden_set/review/evaluation_110_r1/ + r2/ + r3/（manual_jd_eval_cases/data_validation/predictions/report 共 12 文件） | D 删除（12 文件） | — | OTHER_GOLDEN_SET_ASSET | 历史 r1/r2/r3 人工 JD 评测中间产物，已由 final/jd_golden_110.jsonl 正式集与 unified_jd_audit 取代 | 否 | 否 | 否 | 否 | 否 | 否 |

- 结论：新增/修改资产均为**算法/测试评测集**（学历语义、匹配 v3、岗位域图谱）与历史人工评测中间产物清理，**不纳入 Gold110 主封板口径**，不影响本次 Final Seal 任何 Gate 检查；安全扫描已一并覆盖上述新增文件。

## 【已知异常 / 剩余风险】
1. Official Career50 有 2 条 source-reported future publish_time（7663333383728597301, 7660694249809692981）——官方站点返回原值，按 SOURCE_REPORTED_FUTURE_TIME 保留，不修正。
2. 统一审计 P2=2、P3=17 为非阻断项（P0/P1=0），按既有审计结论留痕。
3. Gold110 requirements 字段存在历史已知空值（final_acceptance_summary: Gold_req_empty=1），属标注口径内信息项，非阻断。
4. ~~网络受限风险已解除~~：09-08 `git -c http.version=HTTP/1.1 fetch` 恢复后，基线已 ff-only 刷新至 origin/develop=0c111e97d26a（本轮审计即基于该最新基线）。
5. 基线刷新后 golden_set/ 新增 3 个算法/测试评测集文件、清理 12 个历史人工评测文件，均不属核心四类资产，不影响封板口径（详见【Post-baseline Golden-set Changes】）。
6. LLM Baseline（evaluation/llm_baseline_110）因 opencode-go CreditsError 余额不足处于 SMOKE_BLOCKED，属模型服务侧问题，与数据资产封板无关。

## 【提交准备度】DATA_ASSET_MANIFEST
- Gold110 正式文件：✅ 存在/可解析/110 条/SHA 一致
- Gold 数据字典：✅ jd_golden_110_data_dictionary.md 存在
- Zhilian 候选数据：✅ raw/clean/review/rejected 齐备
- Official Career 数据：✅ raw/clean/gold 齐备
- 统一审计报告：✅ unified_jd_audit 留痕完整（final_acceptance_summary.json 等 12 文件）
- Gold exclusion manifest：✅ 110 行、覆盖 110/110
- 训练许可口径：Gold=评测专用（exclusion manifest 明确排除训练）；其余候选池无明确项目规则处一律标注 NOT_DEFINED_DO_NOT_ASSUME，未做猜测。

## 【最终 Gate】
**DATA_MODULE_READY_FOR_FINAL_SUBMISSION**

| 检查项 | 结果 |
|---|---|
| P0_eq_0 | ✅ |
| P1_eq_0 | ✅ |
| gold_unmodified_vs_known_sha | ✅ |
| gold_parseable | ✅ |
| gold_records_110 | ✅ |
| gold_sample_id_unique | ✅ |
| zhilian_sha_no_mismatch | ✅ |
| exclusion_manifest_complete | ✅ |
| no_secret_leak | ✅ |
| key_files_readable | ✅ |
| no_parse_errors | ✅ |
| cross_source_no_unexpected_overlap | ✅ |
| regression_T1_T2_ok | ✅ |
