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
  "title_raw_exact_accuracy": 0.45454545454545453,
  "title_normalized_accuracy": 0.7272727272727273,
  "skills_micro": {
    "tp": 70,
    "fp": 46,
    "fn": 18,
    "precision": 0.603448275862069,
    "recall": 0.7954545454545454,
    "f1": 0.6862745098039215
  },
  "skills_average_sample_f1": 0.6845765345765346,
  "bonus_skills_micro": {
    "tp": 7,
    "fp": 8,
    "fn": 13,
    "precision": 0.4666666666666667,
    "recall": 0.35,
    "f1": 0.4
  },
  "bonus_skills_average_sample_f1": 0.20909090909090908,
  "education_raw_exact_accuracy": 0.9090909090909091,
  "experience": "Schema coverage gap: current JDExtractionResult schema has no experience_range field",
  "core_duties": "Schema coverage gap: current JDExtractionResult schema has no core_duties field"
}
```

空学历 gold 以 `null / No explicit education requirement` 参与 raw exact 对比：模型同样未输出学历即为正确，凭空输出学历即为错误。经验与核心职责没有对应的 `JDExtractionResult` 字段，属于 Schema coverage gap，不会伪造预测或准确率。`skills` 与 `requirements[nice]` 分别作为必备技能和加分技能的可观测输出；该映射应在后续算法评审中确认。历史 0.6112 未参与本报告。

## Per-JD results

- jd_012: title raw=False, normalized=False; skills TP/FP/FN=['Go', 'Java', 'Python']/['ERP']/[], F1=0.8571; bonus TP/FP/FN=[]/['ERP']/[], F1=0.0000; education=True
- jd_030: title raw=False, normalized=True; skills TP/FP/FN=['数据分析']/['AIGC创作', 'Prompt', 'Python', 'SQL', '可视化分析', '多模态大模型', '数据处理', '自动化评测脚本', '评测方案', '音视频质量评估']/['AIGC', '大模型评测'], F1=0.1429; bonus TP/FP/FN=['Python', 'SQL']/['可视化分析', '多模态大模型', '数据处理', '自动化评测脚本']/['多模态模型', '数据可视化', '数据标注', '自动化评测'], F1=0.3333; education=True
- public_001: title raw=False, normalized=False; skills TP/FP/FN=['Odoo', 'Python']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=0.0000; education=True
- public_002: title raw=True, normalized=True; skills TP/FP/FN=['Java', 'JavaScript', 'Python', 'Shell']/['B/S系统测试', 'C/S系统测试', '大数据测试', '性能测试', '测试流程', '测试理论', '自动化测试']/['JIRA', 'QC', '软件测试'], F1=0.4444; bonus TP/FP/FN=['大数据测试', '性能测试']/[]/['自动化测试'], F1=0.8000; education=True
- public_003: `fallback` — LLMExtractionError: 所有 provider 均失败: provider 'opencode-go' 调用异常: <failed_attempts>

<generation number="1">
<exception>
    1 validation error for JDExtractionResult
  Invalid JSON: expected value at line 1 column 47 [type=json_invalid, input_value='{"position_name": "算...合作", "主动性"]}', input_type=str]
    For further information visit https://errors.pydantic.dev/2.13/v/json_invalid
</exception>
<completion>
    ChatCompletion(id='70a5f3fa-bacd-4dfc-9730-2ebc6817a3b4', choices=[Choice(finish_reason='tool_calls', index=0, logprobs=None, message=ChatCompletionMessage(content='', refusal=None, role='assistant', annotations=None, audio=None, function_call=None, tool_calls=[ChatCompletionMessageFunctionToolCall(id='call_00_WfS5KMBkSrAGSpliVSQs1449', function=Function(arguments='{"position_name": "算法工程师", "level": 初级, "skills": [{"name": "机器学习"}, {"name": "深度学习"}, {"name": "图计算"}, {"name": "TensorFlow"}, {"name": "PyTorch"}, {"name": "Hive"}, {"name": "数据结构"}, {"name": "自然语言处理"}, {"name": "数据挖掘"}, {"name": "大语言模型"}, {"name": "推荐系统"}, {"name": "搜索引擎"}, {"name": "计算广告"}, {"name": "图像"}, {"name": "风控"}, {"name": "智能客服"}], "tools": [{"name": "TensorFlow"}, {"name": "PyTorch"}, {"name": "Hive"}, {"name": "Linux"}], "education": {"level": "硕士", "major": "计算机"}, "certifications": [], "requirements": [{"skill_name": "机器学习", "necessity": "must", "level": "初级"}, {"skill_name": "深度学习", "necessity": "must", "level": "初级"}, {"skill_name": "图计算", "necessity": "must", "level": "初级"}, {"skill_name": "TensorFlow", "necessity": "nice", "level": "初级"}, {"skill_name": "PyTorch", "necessity": "nice", "level": "初级"}, {"skill_name": "Hive", "necessity": "nice", "level": "初级"}, {"skill_name": "数据结构", "necessity": "must", "level": "中级"}, {"skill_name": "自然语言处理", "necessity": "must", "level": "初级"}, {"skill_name": "数据挖掘", "necessity": "must", "level": "初级"}, {"skill_name": "大语言模型", "necessity": "nice"}, {"skill_name": "推荐系统", "necessity": "nice"}, {"skill_name": "搜索引擎", "necessity": "nice"}, {"skill_name": "计算广告", "necessity": "nice"}, {"skill_name": "图像", "necessity": "nice"}, {"skill_name": "风控", "necessity": "nice"}, {"skill_name": "智能客服", "necessity": "nice"}], "soft_skills": ["团队合作", "主动性"]}', name='JDExtractionResult'), type='function', index=0)]))], created=1786256922, model='deepseek-v4-flash', object='chat.completion', moderation=None, service_tier=None, system_fingerprint=None, usage=CompletionUsage(completion_tokens=601, prompt_tokens=2929, total_tokens=3530, completion_tokens_details=CompletionTokensDetails(accepted_prediction_tokens=None, audio_tokens=0, reasoning_tokens=0, rejected_prediction_tokens=None), prompt_tokens_details=PromptTokensDetails(audio_tokens=0, cache_write_tokens=None, cached_tokens=2560), prompt_cache_hit_tokens=2560, prompt_cache_miss_tokens=369), cost='0')
</completion>
</generation>

<generation number="2">
<exception>
    1 validation error for JDExtractionResult
position_name
  Field required [type=missing, input_value={'arguments': '{"position...合作", "主动性"]}'}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/missing
</exception>
<completion>
    ChatCompletion(id='ce8e692d-1b77-42e8-8ad8-764d23deb9e9', choices=[Choice(finish_reason='tool_calls', index=0, logprobs=None, message=ChatCompletionMessage(content='', refusal=None, role='assistant', annotations=None, audio=None, function_call=None, tool_calls=[ChatCompletionMessageFunctionToolCall(id='call_00_JOSFHVBeot2PtEDDXzrS3009', function=Function(arguments='{"arguments": "{\\"position_name\\": \\"算法工程师\\", \\"level\\": \\"初级\\", \\"skills\\": [{\\"name\\": \\"机器学习\\"}, {\\"name\\": \\"深度学习\\"}, {\\"name\\": \\"图计算\\"}, {\\"name\\": \\"TensorFlow\\"}, {\\"name\\": \\"PyTorch\\"}, {\\"name\\": \\"Hive\\"}, {\\"name\\": \\"数据结构\\"}, {\\"name\\": \\"自然语言处理\\"}, {\\"name\\": \\"数据挖掘\\"}, {\\"name\\": \\"大语言模型\\"}, {\\"name\\": \\"推荐系统\\"}, {\\"name\\": \\"搜索引擎\\"}, {\\"name\\": \\"计算广告\\"}, {\\"name\\": \\"图像\\"}, {\\"name\\": \\"风控\\"}, {\\"name\\": \\"智能客服\\"}], \\"tools\\": [{\\"name\\": \\"TensorFlow\\"}, {\\"name\\": \\"PyTorch\\"}, {\\"name\\": \\"Hive\\"}, {\\"name\\": \\"Linux\\"}], \\"education\\": {\\"level\\": \\"硕士\\", \\"major\\": \\"计算机\\"}, \\"certifications\\": [], \\"requirements\\": [{\\"skill_name\\": \\"机器学习\\", \\"necessity\\": \\"must\\", \\"level\\": \\"初级\\"}, {\\"skill_name\\": \\"深度学习\\", \\"necessity\\": \\"must\\", \\"level\\": \\"初级\\"}, {\\"skill_name\\": \\"图计算\\", \\"necessity\\": \\"must\\", \\"level\\": \\"初级\\"}, {\\"skill_name\\": \\"TensorFlow\\", \\"necessity\\": \\"nice\\", \\"level\\": \\"初级\\"}, {\\"skill_name\\": \\"PyTorch\\", \\"necessity\\": \\"nice\\", \\"level\\": \\"初级\\"}, {\\"skill_name\\": \\"Hive\\", \\"necessity\\": \\"nice\\", \\"level\\": \\"初级\\"}, {\\"skill_name\\": \\"数据结构\\", \\"necessity\\": \\"must\\", \\"level\\": \\"中级\\"}, {\\"skill_name\\": \\"自然语言处理\\", \\"necessity\\": \\"must\\", \\"level\\": \\"初级\\"}, {\\"skill_name\\": \\"数据挖掘\\", \\"necessity\\": \\"must\\", \\"level\\": \\"初级\\"}, {\\"skill_name\\": \\"大语言模型\\", \\"necessity\\": \\"nice\\"}, {\\"skill_name\\": \\"推荐系统\\", \\"necessity\\": \\"nice\\"}, {\\"skill_name\\": \\"搜索引擎\\", \\"necessity\\": \\"nice\\"}, {\\"skill_name\\": \\"计算广告\\", \\"necessity\\": \\"nice\\"}, {\\"skill_name\\": \\"图像\\", \\"necessity\\": \\"nice\\"}, {\\"skill_name\\": \\"风控\\", \\"necessity\\": \\"nice\\"}, {\\"skill_name\\": \\"智能客服\\", \\"necessity\\": \\"nice\\"}], \\"soft_skills\\": [\\"团队合作\\", \\"主动性\\"]}"}', name='JDExtractionResult'), type='function', index=0)]))], created=1786256941, model='deepseek-v4-flash', object='chat.completion', moderation=None, service_tier=None, system_fingerprint=None, usage=CompletionUsage(completion_tokens=1130, prompt_tokens=6489, total_tokens=7619, completion_tokens_details=CompletionTokensDetails(accepted_prediction_tokens=None, audio_tokens=0, reasoning_tokens=0, rejected_prediction_tokens=None), prompt_tokens_details=PromptTokensDetails(audio_tokens=0, cache_write_tokens=None, cached_tokens=5376), prompt_cache_hit_tokens=2816, prompt_cache_miss_tokens=744), cost='0')
</completion>
</generation>

</failed_attempts>

<last_exception>
    1 validation error for JDExtractionResult
position_name
  Field required [type=missing, input_value={'arguments': '{"position...合作", "主动性"]}'}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/missing
</last_exception>
- public_004: title raw=True, normalized=True; skills TP/FP/FN=['Ansible', 'ELK', 'Git', 'Grafana', 'Jenkins', 'Kubernetes', 'Linux', 'Prometheus', 'Python', 'Shell']/['可观测性', '微服务']/[], F1=0.9091; bonus TP/FP/FN=[]/['微服务']/['AWS', 'Azure', 'GCP', '微服务架构运维'], F1=0.0000; education=True
- public_005: title raw=False, normalized=False; skills TP/FP/FN=['Windows', 'macOS', '故障诊断', '服务器维护', '网络']/[]/['Office'], F1=0.9091; bonus TP/FP/FN=[]/[]/[], F1=0.0000; education=True
- public_006: title raw=True, normalized=True; skills TP/FP/FN=['数据分析', '数据可视化', '数据建模', '机器学习', '统计学']/['数学', '数据清洗']/['项目管理'], F1=0.7692; bonus TP/FP/FN=[]/[]/[], F1=0.0000; education=True
- public_007: title raw=True, normalized=True; skills TP/FP/FN=['Apache Spark', 'Elasticsearch', 'Pandas', 'PyTorch', 'Python', 'SQL', 'TensorFlow', '多模态模型', '大语言模型', '机器学习', '模型评估', '深度学习', '特征工程']/['AB测试', '数学建模', '模型微调']/['A/B测试'], F1=0.8667; bonus TP/FP/FN=[]/[]/['国产算力研发'], F1=0.0000; education=True
- public_008: title raw=False, normalized=True; skills TP/FP/FN=['C++', 'DeepSpeed', 'Megatron', 'PyTorch', 'Python', 'TensorRT-LLM', 'vLLM', '增量预训练', '大语言模型']/['AGENT', 'ChatBI', 'DeepSeek', 'Qwen', '多模态大模型', '大模型微调', '对齐', '封装', '推理', '检索增强生成', '深度学习', '自然语言处理', '计算机视觉', '语音交互', '调优', '部署', '部署加速', '量化']/['LoRA', '模型对齐', '模型部署', '模型量化'], F1=0.4500; bonus TP/FP/FN=['ChatBI', '检索增强生成']/['AGENT', '多模态大模型']/['Agentic AI', '多模态模型'], F1=0.5000; education=True
- public_009: title raw=True, normalized=True; skills TP/FP/FN=['AngularJS', 'Apache Flink', 'Apache Kafka', 'Apache Spark', 'CSS', 'Flume', 'HBase', 'HTML', 'Hadoop', 'Hive', 'JavaScript', 'Linux', 'Perl', 'Python', 'Scala', 'Shell', 'jQuery']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=0.0000; education=False
- public_010: title raw=False, normalized=True; skills TP/FP/FN=['系统运维']/['数据库', '数据采集', '电网业务']/['性能优化', '故障处理', '电网业务知识', '监控', '系统部署', '问题分析'], F1=0.1818; bonus TP/FP/FN=['南方数据中心']/[]/['南方电网项目实施'], F1=0.6667; education=True

## Lowest three skill-F1 cases

- jd_030: skills F1=0.1429; FP=['AIGC创作', 'Prompt', 'Python', 'SQL', '可视化分析', '多模态大模型', '数据处理', '自动化评测脚本', '评测方案', '音视频质量评估']; FN=['AIGC', '大模型评测']
- public_010: skills F1=0.1818; FP=['数据库', '数据采集', '电网业务']; FN=['性能优化', '故障处理', '电网业务知识', '监控', '系统部署', '问题分析']
- public_002: skills F1=0.4444; FP=['B/S系统测试', 'C/S系统测试', '大数据测试', '性能测试', '测试流程', '测试理论', '自动化测试']; FN=['JIRA', 'QC', '软件测试']

## Main automatically classifiable error types

- possible priority/OR-condition interpretation issue (text marker + set difference): 9
- model-added skills not in human gold: 8
- human-gold skills missed: 7
- skills have both additions and omissions: 6
- required/bonus skill mixing: 3
- title normalization masks a raw-title difference (manual over-normalization check needed): 3

Priority/OR conditions are only flagged when a deterministic text marker co-occurs with a set difference; the current schema does not encode condition logic, so these are review candidates rather than conclusive errors.

## Final baseline consolidation

This is the final real-LLM baseline run. `total_samples = 12`,
`real_llm_success_samples = 11`, `fallback_samples = 1`, and
`failed_samples = 0`. Every quantitative metric in this report is calculated
only from the 11 `real_llm_success` records; no rule-fallback output is used as
a real-LLM prediction.

`public_003` (算法工程师（拼多多集团）) is the sole fallback. The provider
returned a response, but Pydantic/instructor structured-output validation could
not construct `JDExtractionResult`: the result was wrapped in `arguments` and
the top-level required `position_name` was missing. One retry produced the same
failure. The sample remains in the 12-record dataset, is excluded from the
real-LLM metrics, and is retained as a baseline robustness issue.

| Metric | Final value |
|---|---:|
| Title raw exact accuracy | 0.4545 |
| Title normalized accuracy | 0.7273 |
| Required skills micro precision | 0.6034 |
| Required skills micro recall | 0.7955 |
| Required skills micro F1 | 0.6863 |
| Required skills average sample F1 | 0.6846 |
| Bonus skills micro precision | 0.4667 |
| Bonus skills micro recall | 0.3500 |
| Bonus skills micro F1 | 0.4000 |
| Bonus skills average sample F1 | 0.2091 |
| Education accuracy | 0.9091 |

Experience and core duties are **Schema coverage gaps**: the current
`JDExtractionResult` schema exposes neither field, so no end-to-end metric is
reported for them. Historical `0.6112` is an older whitelist baseline, not a
real-LLM result, and is not used in this report.
