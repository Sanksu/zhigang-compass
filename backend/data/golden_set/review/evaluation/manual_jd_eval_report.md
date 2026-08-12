# A01 人工 JD 集端到端评测报告

本报告只把 `real_llm_success` 行计入指标；`fallback` 和 `failed` 行保留在逐条结果中，但绝不计入指标。

## 当前真实链路

`JDExtractor.extract` → `LLMProviderChain.extract_structured` → `post_process`。岗位名归一化只用于评测侧的 `normalize_position_name` 对照；`PositionAligner`（Neo4j/SBERT）不在 `JDExtractor.extract` 内。

## 指标

```json
{
  "total_samples": 12,
  "real_llm_success_samples": 12,
  "fallback_samples": 0,
  "failed_samples": 0,
  "title_raw_exact_accuracy": 0.3333333333333333,
  "title_normalized_accuracy": 0.5,
  "skills_micro": {
    "tp": 82,
    "fp": 67,
    "fn": 12,
    "precision": 0.5503355704697986,
    "recall": 0.8723404255319149,
    "f1": 0.6748971193415638
  },
  "skills_average_sample_f1": 0.6835241512873091,
  "bonus_skills_micro": {
    "tp": 12,
    "fp": 20,
    "fn": 16,
    "precision": 0.375,
    "recall": 0.42857142857142855,
    "f1": 0.39999999999999997
  },
  "bonus_skills_average_sample_f1": 0.22564102564102564,
  "education_raw_exact_accuracy": 0.9166666666666666,
  "experience": "Schema coverage gap: current JDExtractionResult schema has no experience_range field",
  "core_duties": "Schema coverage gap: current JDExtractionResult schema has no core_duties field",
  "per_sample_skills_f1": [
    0.75,
    0.25,
    1.0,
    0.6667,
    0.5714,
    0.8333,
    0.9091,
    0.5263,
    0.75,
    0.5455,
    0.9714,
    0.4286
  ],
  "per_sample_bonus_f1": [
    0.0,
    0.3077,
    0.0,
    1.0,
    0.5,
    0.4,
    0.0,
    0.0,
    0.0,
    0.5,
    0.0,
    0.0
  ],
  "error_types": [
    [
      "possible priority/OR-condition interpretation issue (text marker + set difference)",
      11
    ],
    [
      "model-added skills not in human gold",
      10
    ],
    [
      "human-gold skills missed",
      7
    ],
    [
      "required/bonus skill mixing",
      7
    ],
    [
      "skills have both additions and omissions",
      6
    ],
    [
      "title normalization masks a raw-title difference (manual over-normalization check needed)",
      2
    ]
  ]
}
```

空学历 gold 以 `null / No explicit education requirement` 参与 raw exact 对比：模型同样未输出学历即为正确，凭空输出学历即为错误。经验与核心职责没有对应的 `JDExtractionResult` 字段，属于 Schema coverage gap，不会伪造预测或准确率。`skills` 与 `requirements[nice]` 分别作为必备技能和加分技能的可观测输出；该映射应在后续算法评审中确认。历史 0.6112 未参与本报告。

## Per-JD results

- jd_012: title raw=False, normalized=False; skills TP/FP/FN=['Go', 'Java', 'Python']/['计算机基础', '软件工程']/[], F1=0.7500; bonus TP/FP/FN=[]/[]/[], F1=0.0000; education=True
- jd_030: title raw=False, normalized=False; skills TP/FP/FN=['AIGC', '数据分析']/['Prompt', 'Python', 'SQL', '可视化分析', '多模态大模型', '数据标注', '竞品调研', '自动化评测脚本', '视频生成', '评测方案', '音视频质量评估']/['大模型评测'], F1=0.2500; bonus TP/FP/FN=['Python', 'SQL']/['可视化分析', '多模态大模型', '竞品调研', '自动化评测脚本', '视频生成']/['多模态模型', '数据可视化', '数据标注', '自动化评测'], F1=0.3077; education=True
- public_001: title raw=False, normalized=False; skills TP/FP/FN=['Odoo', 'Python']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=0.0000; education=True
- public_002: title raw=False, normalized=True; skills TP/FP/FN=['Java', 'JavaScript', 'Python', 'Shell', '软件测试']/['大数据测试', '性能测试', '自动化测试']/['JIRA', 'QC'], F1=0.6667; bonus TP/FP/FN=['大数据测试', '性能测试', '自动化测试']/[]/[], F1=1.0000; education=True
- public_003: title raw=True, normalized=True; skills TP/FP/FN=['Linux', '图计算', '数据挖掘', '机器学习', '深度学习', '自然语言处理']/['Hive', 'PyTorch', 'TensorFlow', '大语言模型', '推荐', '搜索引擎', '数据结构', '用户增长', '计算广告']/[], F1=0.5714; bonus TP/FP/FN=['Hive', 'PyTorch', 'TensorFlow', '大语言模型']/['推荐', '搜索引擎', '用户增长', '计算广告']/['广告算法', '推荐算法', '计算机视觉', '风控算法'], F1=0.5000; education=True
- public_004: title raw=True, normalized=True; skills TP/FP/FN=['Ansible', 'ELK', 'Git', 'Grafana', 'Jenkins', 'Kubernetes', 'Linux', 'Prometheus', 'Python', 'Shell']/['可观测性', '告警', '微服务', '日志']/[], F1=0.8333; bonus TP/FP/FN=['微服务']/[]/['AWS', 'Azure', 'GCP'], F1=0.4000; education=True
- public_005: title raw=False, normalized=False; skills TP/FP/FN=['Office', 'Windows', 'macOS', '故障诊断', '服务器维护']/[]/['网络'], F1=0.9091; bonus TP/FP/FN=[]/[]/[], F1=0.0000; education=True
- public_006: title raw=True, normalized=True; skills TP/FP/FN=['数据分析', '数据可视化', '机器学习', '统计学', '项目管理']/['报表制作', '数据挖掘', '数据收集', '数据模型构建', '数据清洗', '数据监测', '用户画像', '预测模型']/['数据建模'], F1=0.5263; bonus TP/FP/FN=[]/['CDA', 'CPDA', '项目管理']/[], F1=0.0000; education=True
- public_007: title raw=False, normalized=False; skills TP/FP/FN=['Apache Spark', 'Elasticsearch', 'Pandas', 'PyTorch', 'Python', 'SQL', 'TensorFlow', '大语言模型', '机器学习', '模型评估', '深度学习', '特征工程']/['AB测试', '强化学习', '数学建模', '模型微调', '模型架构', '模型融合']/['A/B测试', '多模态模型'], F1=0.7500; bonus TP/FP/FN=[]/['大语言模型', '强化学习', '模型微调', '模型架构', '模型融合']/['国产算力研发'], F1=0.0000; education=True
- public_008: title raw=False, normalized=False; skills TP/FP/FN=['C++', 'DeepSpeed', 'Megatron', 'PyTorch', 'Python', 'TensorRT-LLM', 'vLLM', '增量预训练', '大语言模型', '模型对齐', '模型部署', '模型量化']/['AGENT', 'ChatBI', 'DeepSeek', 'Qwen', '图表理解', '多智能体', '多模态大模型', '大模型微调', '报告生成', '数据联动', '检索增强生成', '模型封装', '模型推理', '模型调优', '深度学习', '知识问答', '自然语言处理', '计算机视觉', '语音交互']/['LoRA'], F1=0.5455; bonus TP/FP/FN=['ChatBI', '检索增强生成']/['AGENT', '多模态大模型']/['Agentic AI', '多模态模型'], F1=0.5000; education=True
- public_009: title raw=True, normalized=True; skills TP/FP/FN=['AngularJS', 'Apache Flink', 'Apache Kafka', 'Apache Spark', 'CSS', 'Flume', 'HBase', 'HTML', 'Hadoop', 'Hive', 'JavaScript', 'Linux', 'Perl', 'Python', 'Scala', 'Shell', 'jQuery']/['数据分析']/[], F1=0.9714; bonus TP/FP/FN=[]/[]/[], F1=0.0000; education=False
- public_010: title raw=False, normalized=True; skills TP/FP/FN=['性能调优', '系统运维', '问题分析']/['故障处置', '数据库', '数据采集', '电网业务']/['故障处理', '电网业务知识', '监控', '系统部署'], F1=0.4286; bonus TP/FP/FN=[]/['电网业务']/['南方数据中心', '南方电网项目实施'], F1=0.0000; education=True

## Lowest three skill-F1 cases

- jd_030: skills F1=0.2500; FP=['Prompt', 'Python', 'SQL', '可视化分析', '多模态大模型', '数据标注', '竞品调研', '自动化评测脚本', '视频生成', '评测方案', '音视频质量评估']; FN=['大模型评测']
- public_010: skills F1=0.4286; FP=['故障处置', '数据库', '数据采集', '电网业务']; FN=['故障处理', '电网业务知识', '监控', '系统部署']
- public_006: skills F1=0.5263; FP=['报表制作', '数据挖掘', '数据收集', '数据模型构建', '数据清洗', '数据监测', '用户画像', '预测模型']; FN=['数据建模']

## Main automatically classifiable error types

- possible priority/OR-condition interpretation issue (text marker + set difference): 11
- model-added skills not in human gold: 10
- human-gold skills missed: 7
- required/bonus skill mixing: 7
- skills have both additions and omissions: 6
- title normalization masks a raw-title difference (manual over-normalization check needed): 2

Priority/OR conditions are only flagged when a deterministic text marker co-occurs with a set difference; the current schema does not encode condition logic, so these are review candidates rather than conclusive errors.
