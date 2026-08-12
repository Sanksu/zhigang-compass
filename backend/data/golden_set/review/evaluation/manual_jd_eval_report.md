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
  "title_normalized_accuracy": 0.6666666666666666,
  "skills_micro": {
    "tp": 82,
    "fp": 23,
    "fn": 12,
    "precision": 0.780952380952381,
    "recall": 0.8723404255319149,
    "f1": 0.8241206030150755
  },
  "skills_average_sample_f1": 0.782933732933733,
  "bonus_skills_micro": {
    "tp": 21,
    "fp": 21,
    "fn": 7,
    "precision": 0.5,
    "recall": 0.75,
    "f1": 0.6
  },
  "bonus_skills_average_sample_f1": 0.37752525252525254,
  "education_raw_exact_accuracy": 1.0,
  "experience": "Schema coverage gap: current JDExtractionResult schema has no experience_range field",
  "core_duties": "Schema coverage gap: current JDExtractionResult schema has no core_duties field"
}
```

空学历 gold 以 `null / No explicit education requirement` 参与 raw exact 对比：模型同样未输出学历即为正确，凭空输出学历即为错误。经验与核心职责没有对应的 `JDExtractionResult` 字段，属于 Schema coverage gap，不会伪造预测或准确率。`skills` 与 `requirements[nice]` 分别作为必备技能和加分技能的可观测输出；该映射应在后续算法评审中确认。历史 0.6112 未参与本报告。

## Per-JD results

- jd_012: title raw=False, normalized=False; skills TP/FP/FN=['Go', 'Java', 'Python']/['计算机基础', '软件工程']/[], F1=0.7500; bonus TP/FP/FN=[]/['ERP']/[], F1=0.0000; education=True
- jd_030: title raw=False, normalized=True; skills TP/FP/FN=['数据分析']/['AIGC创作', '评测方案', '音视频质量评估']/['AIGC', '大模型评测'], F1=0.2857; bonus TP/FP/FN=['Python', 'SQL', '多模态模型', '数据可视化', '数据标注', '自动化评测']/['AI算法测试', '文生图', '文生视频', '竞品评测', '评测体系建设', '音频生成']/[], F1=0.6667; education=True
- public_001: title raw=False, normalized=False; skills TP/FP/FN=['Odoo', 'Python']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=0.0000; education=True
- public_002: title raw=False, normalized=True; skills TP/FP/FN=['JIRA', 'Java', 'JavaScript', 'Python', 'QC', 'Shell']/['测试流程', '测试理论', '需求分析']/['软件测试'], F1=0.7500; bonus TP/FP/FN=['大数据测试', '性能测试', '自动化测试']/[]/[], F1=1.0000; education=True
- public_003: title raw=True, normalized=True; skills TP/FP/FN=['图计算', '机器学习', '深度学习']/['数据结构']/['Linux', '数据挖掘', '自然语言处理'], F1=0.6000; bonus TP/FP/FN=['Hive', 'PyTorch', 'TensorFlow', '大语言模型']/['互联网风控', '图像', '平台治理', '推荐', '搜索引擎', '数据挖掘', '智能客服', '用户增长', '自然语言处理', '计算广告']/['广告算法', '推荐算法', '计算机视觉', '风控算法'], F1=0.3636; education=True
- public_004: title raw=True, normalized=True; skills TP/FP/FN=['Ansible', 'Git', 'Grafana', 'Jenkins', 'Kubernetes', 'Linux', 'Prometheus', 'Python', 'Shell']/['可观测性', '日志']/['ELK'], F1=0.8571; bonus TP/FP/FN=['AWS', 'Azure', 'GCP', '微服务']/[]/[], F1=1.0000; education=True
- public_005: title raw=False, normalized=True; skills TP/FP/FN=['Windows', 'macOS', '故障诊断', '服务器维护']/['office办公']/['Office', '网络'], F1=0.7273; bonus TP/FP/FN=[]/[]/[], F1=0.0000; education=True
- public_006: title raw=True, normalized=True; skills TP/FP/FN=['数据分析', '数据可视化', '数据建模', '机器学习', '统计学']/['数学', '数据清洗']/['项目管理'], F1=0.7692; bonus TP/FP/FN=[]/['项目管理']/[], F1=0.0000; education=True
- public_007: title raw=False, normalized=False; skills TP/FP/FN=['A/B测试', 'Apache Spark', 'Elasticsearch', 'Pandas', 'PyTorch', 'Python', 'SQL', 'TensorFlow', '大语言模型', '机器学习', '模型评估', '深度学习', '特征工程']/['数学建模']/['多模态模型'], F1=0.9286; bonus TP/FP/FN=[]/['大语言模型']/['国产算力研发'], F1=0.0000; education=True
- public_008: title raw=False, normalized=False; skills TP/FP/FN=['C++', 'DeepSpeed', 'Megatron', 'PyTorch', 'Python', 'TensorRT-LLM', 'vLLM', '增量预训练', '大语言模型', '模型对齐', '模型部署', '模型量化']/['大模型微调', '模型封装', '模型推理', '模型调优', '深度学习', '自然语言处理', '计算机视觉', '语音交互']/['LoRA'], F1=0.7273; bonus TP/FP/FN=['ChatBI', '检索增强生成']/['AGENT', '多模态大模型训练']/['Agentic AI', '多模态模型'], F1=0.5000; education=True
- public_009: title raw=True, normalized=True; skills TP/FP/FN=['AngularJS', 'Apache Flink', 'Apache Kafka', 'Apache Spark', 'CSS', 'Flume', 'HBase', 'HTML', 'Hadoop', 'Hive', 'JavaScript', 'Linux', 'Perl', 'Python', 'Scala', 'Shell', 'jQuery']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=0.0000; education=True
- public_010: title raw=False, normalized=True; skills TP/FP/FN=['性能调优', '故障处理', '电网业务知识', '监控', '系统运维', '系统部署', '问题分析']/[]/[], F1=1.0000; bonus TP/FP/FN=['南方数据中心', '南方电网项目实施']/[]/[], F1=1.0000; education=True

## Lowest three skill-F1 cases

- jd_030: skills F1=0.2857; FP=['AIGC创作', '评测方案', '音视频质量评估']; FN=['AIGC', '大模型评测']
- public_003: skills F1=0.6000; FP=['数据结构']; FN=['Linux', '数据挖掘', '自然语言处理']
- public_005: skills F1=0.7273; FP=['office办公']; FN=['Office', '网络']

## Main automatically classifiable error types

- model-added skills not in human gold: 9
- possible priority/OR-condition interpretation issue (text marker + set difference): 9
- human-gold skills missed: 8
- skills have both additions and omissions: 8
- title normalization masks a raw-title difference (manual over-normalization check needed): 4
- required/bonus skill mixing: 3

Priority/OR conditions are only flagged when a deterministic text marker co-occurs with a set difference; the current schema does not encode condition logic, so these are review candidates rather than conclusive errors.
