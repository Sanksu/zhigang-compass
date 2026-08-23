# A01 人工 JD 集端到端评测报告

本报告只把 `real_llm_success` 行计入指标；`fallback` 和 `failed` 行保留在逐条结果中，但绝不计入指标。

## 当前真实链路

`JDExtractor.extract` → `LLMProviderChain.extract_structured` → `post_process`。岗位名归一化只用于评测侧的 `normalize_position_name` 对照；`PositionAligner`（Neo4j/SBERT）不在 `JDExtractor.extract` 内。

## 指标

```json
{
  "total_samples": 12,
  "real_llm_success_samples": 11,
  "fallback_samples": 1,
  "failed_samples": 0,
  "title_raw_exact_accuracy": 0.9090909090909091,
  "title_normalized_accuracy": 0.9090909090909091,
  "skills_micro": {
    "tp": 77,
    "fp": 21,
    "fn": 13,
    "precision": 0.7857142857142857,
    "recall": 0.8555555555555555,
    "f1": 0.8191489361702127
  },
  "skills_average_sample_f1": 0.8029030088838701,
  "bonus_skills_micro": {
    "tp": 17,
    "fp": 15,
    "fn": 9,
    "precision": 0.53125,
    "recall": 0.6538461538461539,
    "f1": 0.5862068965517242
  },
  "bonus_skills_average_sample_f1": 0.3409090909090909,
  "education_raw_exact_accuracy": 1.0,
  "experience": "Schema coverage gap: current JDExtractionResult schema has no experience_range field",
  "core_duties": "Schema coverage gap: current JDExtractionResult schema has no core_duties field",
  "per_sample_skills_f1": [
    0.8571,
    0.2857,
    1.0,
    0.6316,
    1.0,
    0.9091,
    0.8,
    0.8333,
    0.8462,
    0.9189,
    0.75
  ],
  "per_sample_bonus_f1": [
    0.0,
    0.8,
    0.0,
    0.8,
    0.4,
    1.0,
    0.0,
    0.0,
    0.0,
    0.75,
    0.0
  ],
  "error_types": [
    [
      "possible priority/OR-condition interpretation issue (text marker + set difference)",
      10
    ],
    [
      "model-added skills not in human gold",
      8
    ],
    [
      "human-gold skills missed",
      7
    ],
    [
      "skills have both additions and omissions",
      6
    ],
    [
      "required/bonus skill mixing",
      3
    ]
  ]
}
```

空学历 gold 以 `null / No explicit education requirement` 参与 raw exact 对比：模型同样未输出学历即为正确，凭空输出学历即为错误。经验与核心职责没有对应的 `JDExtractionResult` 字段，属于 Schema coverage gap，不会伪造预测或准确率。`skills` 与 `requirements[nice]` 分别作为必备技能和加分技能的可观测输出；该映射应在后续算法评审中确认。历史 0.6112 未参与本报告。

## Per-JD results

- jd_012: title raw=True, normalized=True; skills TP/FP/FN=['Go', 'Java', 'Python']/['JavaScript']/[], F1=0.8571; bonus TP/FP/FN=[]/['ERP']/[], F1=0.0000; education=True
- jd_030: title raw=True, normalized=True; skills TP/FP/FN=['AIGC']/['Python', 'SQL', '数据可视化', '自动化评测']/['数据分析'], F1=0.2857; bonus TP/FP/FN=['Python', 'SQL', '数据可视化', '自动化评测']/[]/['多模态模型', '数据标注'], F1=0.8000; education=True
- public_001: title raw=True, normalized=True; skills TP/FP/FN=['Odoo', 'Python']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=0.0000; education=True
- public_002: title raw=True, normalized=True; skills TP/FP/FN=['JIRA', 'Java', 'JavaScript', 'Python', 'QC', 'Shell']/['APP测试', 'UI测试', '大数据测试', '性能测试', '接口测试', '自动化测试']/['软件测试'], F1=0.6316; bonus TP/FP/FN=['大数据测试', '性能测试']/[]/['自动化测试'], F1=0.8000; education=True
- public_003: title raw=True, normalized=True; skills TP/FP/FN=['Linux', '图计算', '数据挖掘', '机器学习', '深度学习', '自然语言处理']/[]/[], F1=1.0000; bonus TP/FP/FN=['Hive', 'PyTorch', 'TensorFlow', '大语言模型']/['互联网风控', '图像', '平台治理', '推荐', '搜索引擎', '智能客服', '用户增长', '计算广告']/['广告算法', '推荐算法', '计算机视觉', '风控算法'], F1=0.4000; education=True
- public_004: title raw=True, normalized=True; skills TP/FP/FN=['Ansible', 'ELK', 'Git', 'Grafana', 'Jenkins', 'Kubernetes', 'Linux', 'Prometheus', 'Python', 'Shell']/['可观测性', '容器化']/[], F1=0.9091; bonus TP/FP/FN=['AWS', 'Azure', 'GCP', '微服务']/[]/[], F1=1.0000; education=True
- public_005: title raw=True, normalized=True; skills TP/FP/FN=['Office', 'Windows', 'macOS', '故障诊断']/[]/['服务器维护', '网络'], F1=0.8000; bonus TP/FP/FN=[]/[]/[], F1=0.0000; education=True
- public_006: title raw=True, normalized=True; skills TP/FP/FN=['数据分析', '数据可视化', '数据建模', '机器学习', '统计学']/['数据清洗']/['项目管理'], F1=0.8333; bonus TP/FP/FN=[]/['CDA', 'CPDA']/[], F1=0.0000; education=True
- public_007: title raw=True, normalized=True; skills TP/FP/FN=['A/B测试', 'Apache Spark', 'Elasticsearch', 'Pandas', 'Python', 'SQL', '大语言模型', '机器学习', '模型评估', '深度学习', '特征工程']/['数学建模']/['PyTorch', 'TensorFlow', '多模态模型'], F1=0.8462; bonus TP/FP/FN=[]/['国产算力', '多模态模型', '强化学习']/['国产算力研发'], F1=0.0000; education=True
- public_008: title raw=True, normalized=True; skills TP/FP/FN=['C++', 'DeepSeek', 'DeepSpeed', 'Megatron', 'PyTorch', 'Python', 'Qwen', 'TensorRT-LLM', 'vLLM', '增量预训练', '大语言模型', '模型对齐', '模型量化', '深度学习', '自然语言处理', '计算机视觉', '语音交互']/['大模型微调']/['LoRA', '模型部署'], F1=0.9189; bonus TP/FP/FN=['ChatBI', '多模态模型', '检索增强生成']/['AGENT']/['Agentic AI'], F1=0.7500; education=True
- public_009: title raw=False, normalized=False; skills TP/FP/FN=['Apache Flink', 'Apache Kafka', 'Apache Spark', 'Flume', 'HBase', 'Hadoop', 'Hive', 'Linux', 'Perl', 'Python', 'Scala', 'Shell']/['AngularJS', 'CSS', 'HTML', 'JavaScript', 'jQuery']/['数据分析', '需求分析', '项目管理'], F1=0.7500; bonus TP/FP/FN=[]/[]/[], F1=0.0000; education=True
- public_010: `fallback` — LLMExtractionError: provider call failed

## Lowest three skill-F1 cases

- jd_030: skills F1=0.2857; FP=['Python', 'SQL', '数据可视化', '自动化评测']; FN=['数据分析']
- public_002: skills F1=0.6316; FP=['APP测试', 'UI测试', '大数据测试', '性能测试', '接口测试', '自动化测试']; FN=['软件测试']
- public_009: skills F1=0.7500; FP=['AngularJS', 'CSS', 'HTML', 'JavaScript', 'jQuery']; FN=['数据分析', '需求分析', '项目管理']

## Main automatically classifiable error types

- possible priority/OR-condition interpretation issue (text marker + set difference): 10
- model-added skills not in human gold: 8
- human-gold skills missed: 7
- skills have both additions and omissions: 6
- required/bonus skill mixing: 3

Priority/OR conditions are only flagged when a deterministic text marker co-occurs with a set difference; the current schema does not encode condition logic, so these are review candidates rather than conclusive errors.
