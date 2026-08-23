# A01 人工 JD 集端到端评测报告

本报告只把 `real_llm_success` 行计入指标；`fallback` 和 `failed` 行保留在逐条结果中，但绝不计入指标。

## 当前真实链路

`JDExtractor.extract` → `LLMProviderChain.extract_structured` → `post_process`。岗位名归一化只用于评测侧的 `normalize_position_name` 对照；`PositionAligner`（Neo4j/SBERT）不在 `JDExtractor.extract` 内。

## 指标

```json
{
  "total_samples": 32,
  "real_llm_success_samples": 3,
  "fallback_samples": 29,
  "failed_samples": 0,
  "title_raw_exact_accuracy": 1.0,
  "title_normalized_accuracy": 1.0,
  "skills_micro": {
    "tp": 10,
    "fp": 1,
    "fn": 0,
    "precision": 0.9090909090909091,
    "recall": 1.0,
    "f1": 0.9523809523809523
  },
  "skills_average_sample_f1": 0.9523809523809524,
  "bonus_skills_micro": {
    "tp": 4,
    "fp": 1,
    "fn": 2,
    "precision": 0.8,
    "recall": 0.6666666666666666,
    "f1": 0.7272727272727272
  },
  "bonus_skills_average_sample_f1": 0.26666666666666666,
  "education_raw_exact_accuracy": 1.0,
  "experience": "Schema coverage gap: current JDExtractionResult schema has no experience_range field",
  "core_duties": "Schema coverage gap: current JDExtractionResult schema has no core_duties field",
  "per_sample_skills_f1": [
    0.8571,
    1.0,
    1.0
  ],
  "per_sample_bonus_f1": [
    0.0,
    0.8,
    0.0
  ],
  "error_types": [
    [
      "possible priority/OR-condition interpretation issue (text marker + set difference)",
      2
    ],
    [
      "model-added skills not in human gold",
      1
    ],
    [
      "required/bonus skill mixing",
      1
    ]
  ]
}
```

空学历 gold 以 `null / No explicit education requirement` 参与 raw exact 对比：模型同样未输出学历即为正确，凭空输出学历即为错误。经验与核心职责没有对应的 `JDExtractionResult` 字段，属于 Schema coverage gap，不会伪造预测或准确率。`skills` 与 `requirements[nice]` 分别作为必备技能和加分技能的可观测输出；该映射应在后续算法评审中确认。历史 0.6112 未参与本报告。

## Per-JD results

- jd_012: title raw=True, normalized=True; skills TP/FP/FN=['Go', 'Java', 'Python']/['JavaScript']/[], F1=0.8571; bonus TP/FP/FN=[]/['ERP']/[], F1=0.0000; education=True
- jd_030: title raw=True, normalized=True; skills TP/FP/FN=['AIGC', 'Prompt', '数据分析', '数据标注', '视频生成']/[]/[], F1=1.0000; bonus TP/FP/FN=['Python', 'SQL', '数据可视化', '自动化评测']/[]/['多模态模型', '数据标注'], F1=0.8000; education=True
- public_001: title raw=True, normalized=True; skills TP/FP/FN=['Odoo', 'Python']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=0.0000; education=True
- public_002: `fallback` — LLMExtractionError: provider call failed
- public_003: `fallback` — LLMExtractionError: provider call failed
- public_004: `fallback` — LLMExtractionError: provider call failed
- public_005: `fallback` — LLMExtractionError: provider call failed
- public_006: `fallback` — LLMExtractionError: provider call failed
- public_007: `fallback` — LLMExtractionError: provider call failed
- public_008: `fallback` — LLMExtractionError: provider call failed
- public_009: `fallback` — LLMExtractionError: provider call failed
- public_010: `fallback` — LLMExtractionError: provider call failed
- r2_001: `fallback` — LLMExtractionError: provider call failed
- r2_002: `fallback` — LLMExtractionError: provider call failed
- r2_003: `fallback` — LLMExtractionError: provider call failed
- r2_004: `fallback` — LLMExtractionError: provider call failed
- r2_005: `fallback` — LLMExtractionError: provider call failed
- r2_006: `fallback` — LLMExtractionError: provider call failed
- r2_007: `fallback` — LLMExtractionError: provider call failed
- r2_008: `fallback` — LLMExtractionError: provider call failed
- r2_009: `fallback` — LLMExtractionError: provider call failed
- r2_010: `fallback` — LLMExtractionError: provider call failed
- r2_011: `fallback` — LLMExtractionError: provider call failed
- r2_012: `fallback` — LLMExtractionError: provider call failed
- r2_013: `fallback` — LLMExtractionError: provider call failed
- r2_014: `fallback` — LLMExtractionError: provider call failed
- r2_015: `fallback` — LLMExtractionError: provider call failed
- r2_016: `fallback` — LLMExtractionError: provider call failed
- r2_017: `fallback` — LLMExtractionError: provider call failed
- r2_018: `fallback` — LLMExtractionError: provider call failed
- r2_019: `fallback` — LLMExtractionError: provider call failed
- r2_020: `fallback` — LLMExtractionError: provider call failed

## Lowest three skill-F1 cases

- jd_012: skills F1=0.8571; FP=['JavaScript']; FN=[]
- jd_030: skills F1=1.0000; FP=[]; FN=[]
- public_001: skills F1=1.0000; FP=[]; FN=[]

## Main automatically classifiable error types

- possible priority/OR-condition interpretation issue (text marker + set difference): 2
- model-added skills not in human gold: 1
- required/bonus skill mixing: 1

Priority/OR conditions are only flagged when a deterministic text marker co-occurs with a set difference; the current schema does not encode condition logic, so these are review candidates rather than conclusive errors.
