# scripts/archive/ — 一次性脚本归档

> 归档时间：2026-08-15（refactor/code-slim-0815）
> 归档口径：**保守精简——保留代码，移出活跃维护路径**。这些脚本已完成使命
> （一次性数据修复/历史迁移/审计），文档/CI/bootstrap 均无引用，移入本目录
> 不再参与 `ruff check scripts` 与日常维护，但 git 历史与产物（reports/、
> data/golden_set/*.jsonl）完整保留，需要时可随时恢复或参考。

## 归档清单（31 个）

### 数据清理类（10 个）
- `cleanup_duplicate_jds.py` — SimHash 重复 JD 清理
- `cleanup_intern_parttime.py` — 实习/兼职存量清理
- `cleanup_invalid_jobs.py` — 聚合帖/污染岗位清理
- `cleanup_dirty_positions.py` — 2026-08-14 审计 23 脏岗位清理
- `cleanup_isolated_skills.py` — 孤立技能分层清理
- `cleanup_noise_positions.py` — 碎片/业务词空岗清理
- `cleanup_position_fragments.py` — P7 词典部署后岗位碎片清理
- `cleanup_position_names.py` — "高级"后缀脏名清理
- `cleanup_tools.py` — Tool 节点归一化合并
- `dedupe_course_nodes.py` — coursera 重复课程归并

### 回填/重抽类（7 个）
- `backfill_jd_detail.py` — 智联详情正文回填
- `backfill_occupation_definitions.py` — LLM 职业定义回填
- `backfill_quality_checks.py` — 绕开游标死循环补检测
- `after_backfill_reingest.py` — 回填后重触发 batch_extract+aggregate
- `re_extract_industry.py` — industry 全量重抽
- `run_aggregation.py` — zhilian 回填后补聚合
- `history_backfill.py` — 12 周历史回爬

### 迁移/合并/修复类（5 个）
- `migrate_skill_names.py` — 按新规则重放 snapshot
- `merge_skill_conflicts.py` — 技能冲突节点合并
- `restore_rt_edges.py` — 修复 merge_skill_conflicts 旧版 bug 产物
- `align_positions.py` — 存量岗位回填 BELONGS_TO_OCCUPATION
- `apply_occupation_aliases.py` — 别名桥接写库

### 审计/评测类（3 个）
- `audit_future_positions.py` — 低频岗位白名单审计（与 audit_position_whitelist 同域，后者保留）
- `rag_eval.py` — RAG 接地评测（一次性）
- `dryrun_discovery_transition.py` — 发现状态流转 dry-run 验证

### 黄金集生成器（5 个，产物 data/golden_set/*.jsonl 是 evaluate/tune 的输入契约，保留）
- `build_golden_set.py` — JD 黄金集 100
- `build_resume_golden_set.py` — 简历黄金集 50
- `build_match_golden.py` — 匹配黄金集
- `build_inflation_golden.py` — 通胀合成集
- `build_temporal_golden.py` — 时滞合成集

### 实验性替代（1 个）
- `tune_match_bt.py` — Bradley-Terry 匹配调优（被 tune_match_weights 取代）

## 配套测试归档

以下测试与被归档脚本一一对应（脚本归档后测试失去运行对象，随脚本移入
`tests/` 子目录留档，不再被 pytest 收集）：
- `tests/test_backfill_jd_detail.py` → backfill_jd_detail.py
- `tests/test_history_backfill_script.py` → history_backfill.py
- `tests/test_tune_match_bt.py` → tune_match_bt.py

## 恢复方法

```bash
git mv scripts/archive/<脚本名>.py scripts/<脚本名>.py
# 注意：归档脚本内的 sys.path 样板基于 scripts/ 相对深度（parents[1]），
# 从 archive/ 恢复后无需改动；若在 archive/ 内直接运行则需修正 sys.path。
```

## 边界提醒

- 归档脚本若需再次运行，先核对数据库当前 schema/数据状态（多数脚本假设
  当年的表结构与数据形态）。
- 需要重新生成黄金集时，先把对应 `build_*_golden.py` 恢复回 scripts/ 再跑。
