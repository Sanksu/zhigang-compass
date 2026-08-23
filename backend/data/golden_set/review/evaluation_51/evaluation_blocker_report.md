# 人工 JD 端到端评测阻塞报告

## 结论

预检已通过并已显式触发真实 `--run`，但生产抽取链在逐条抽取前初始化失败。未调用模型、未产生 fallback，也未生成模拟预测或指标。

## 预检结果

- 工作簿：`data\golden_set\review\jd_manual_review_merged.xlsx`
- 盲标数据行数：51/51
- 非空正文：51/51；可追溯 URL：51/51
- annotator：'AI-ZCode', 'LQ', 'ZKT'；要求为非空（多人标注各保留本人代号，禁止空标注）
- 全字段格式合格且可纳入真实评测的行数：51/51
- total_samples = 51
- real_llm_success_samples = 0；fallback_samples = 0；failed_samples = 0（未进入逐条抽取）

## 格式异常汇总

- 无

## 运行时阻塞

- `NameError: provider call failed`
- 当前运行时无法导入 PyYAML；未验证 provider/API 配置，且未读取任何密钥。

## 真实链路审计

- 实际入口为 `backend/app/services/extraction/jd_extractor.py:JDExtractor.extract`。
- 真实路径为 `TASK_TEMPLATE + SYSTEM_PROMPT + FEW_SHOT_EXAMPLES` → `LLMProviderChain.extract_structured` → `post_process`。
- `JDExtractor` 在配置缺失或 `LLMExtractionError` 时静默降级到 `_rule_based_extract`（白名单扫描）。因此不能把降级结果称为真实 LLM 端到端评测，更不能与历史 0.6112 白名单扫描数字混为一谈。
- `JDExtractionResult` 有岗位名、skills、education、requirements（must/nice），但没有经验区间或核心职责字段；当前代码无法对这两项产出真实预测。
- `PositionAligner` 的 Neo4j/SBERT 对齐不在 `JDExtractor.extract` 调用链；本评测脚本仅按要求用 `normalize_position_name` 做静态规则对照。

## 恢复条件与命令

1. 确保全部行 annotator 非空（round1/round2 各保留标注者本人代号，如 LQ/张恺天代号）；将非空的 skills、bonus_skills、core_duties 填为 JSON 字符串数组；experience 留空或写 JSON 对象。空学历表示无明确学历要求，合法且不需要补写。
2. 不改变现有标签含义的前提下，重新保存工作簿后运行预检。
3. 只有预检为 12/12 后，才执行真实 LLM 调用。该命令可能产生模型调用费用：

```powershell
cd backend
uv run python tests/evaluate/run_manual_jd_eval.py --run
```

预检命令（不调用网络或 LLM）：

```powershell
cd backend
uv run python tests/evaluate/run_manual_jd_eval.py
```
