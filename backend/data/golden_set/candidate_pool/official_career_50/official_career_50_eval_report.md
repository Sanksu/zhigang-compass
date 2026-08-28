# A01 人工 JD 集端到端评测报告

本报告只把 `real_llm_success` 行计入指标；`fallback` 和 `failed` 行保留在逐条结果中，但绝不计入指标。

## 当前真实链路

`JDExtractor.extract` → `LLMProviderChain.extract_structured` → `post_process`。岗位名归一化只用于评测侧的 `normalize_position_name` 对照；`PositionAligner`（Neo4j/SBERT）不在 `JDExtractor.extract` 内。

## 三口径说明（08-24 证据链）

`skills_micro_llm_only` = 纯模型输出 vs gold（无补漏、无词面豁免）；`skills_micro_raw` = 模型 + 确定性补漏（gold 词 ∩ 正文词面）；`skills_micro_aligned` = 补漏后 + 词面豁免（PR #330 达标口径）。三口径同时归档，防止达标数字掩盖纯模型回退；逐条结果带 `input_sha256`，配合 commit/provider/model/gold_sha256 可同版本回放。

## 指标

```json
{
  "total_samples": 50,
  "real_llm_success_samples": 50,
  "fallback_samples": 0,
  "failed_samples": 0,
  "title_raw_exact_accuracy": 1.0,
  "title_normalized_accuracy": 1.0,
  "skills_micro": {
    "tp": 483,
    "fp": 41,
    "fn": 0,
    "precision": 0.9217557251908397,
    "recall": 1.0,
    "f1": 0.9592850049652433
  },
  "skills_micro_aligned": {
    "tp": 483,
    "fp": 0,
    "fn": 0,
    "precision": 1.0,
    "recall": 1.0,
    "f1": 1.0
  },
  "skills_micro_llm_only": {
    "tp": 451,
    "fp": 41,
    "fn": 32,
    "precision": 0.9166666666666666,
    "recall": 0.9337474120082816,
    "f1": 0.9251282051282051
  },
  "hallucinated_fp": {},
  "provider": "commandcode",
  "model": "deepseek/deepseek-v4-flash-vision-exp",
  "commit": "unknown",
  "eval_spec_version": "20260824-a",
  "gold_sha256": "273afda6ee4b5738365cb1bf38da2bc6f84b7e62e25e2131a2b8460ffd4535bf",
  "skills_average_sample_f1": 0.9482018286664232,
  "bonus_skills_micro": {
    "tp": 136,
    "fp": 18,
    "fn": 22,
    "precision": 0.8831168831168831,
    "recall": 0.8607594936708861,
    "f1": 0.8717948717948718
  },
  "bonus_skills_micro_aligned": {
    "tp": 155,
    "fp": 18,
    "fn": 3,
    "precision": 0.8959537572254336,
    "recall": 0.9810126582278481,
    "f1": 0.9365558912386708
  },
  "bonus_skills_average_sample_f1": 0.7513073593073594,
  "bonus_skills_aligned_average_sample_f1": 0.9082646339168079,
  "education_raw_exact_accuracy": 0.9,
  "experience_accuracy": 0.16,
  "experience_compared": 50,
  "core_duties_micro": {
    "tp": 197,
    "fp": 23,
    "fn": 10,
    "precision": 0.8954545454545455,
    "recall": 0.9516908212560387,
    "f1": 0.9227166276346604
  },
  "per_sample_skills_f1": [
    0.7778,
    0.6667,
    0.6,
    1.0,
    1.0,
    1.0,
    1.0,
    0.9677,
    1.0,
    0.9545,
    1.0,
    1.0,
    1.0,
    1.0,
    1.0,
    1.0,
    1.0,
    1.0,
    1.0,
    0.9583,
    1.0,
    1.0,
    0.963,
    1.0,
    1.0,
    1.0,
    0.6667,
    1.0,
    0.9756,
    1.0,
    1.0,
    1.0,
    1.0,
    0.8333,
    0.8333,
    0.8571,
    0.8889,
    0.9091,
    1.0,
    1.0,
    1.0,
    1.0,
    1.0,
    0.9231,
    1.0,
    1.0,
    1.0,
    1.0,
    0.8571,
    0.7778
  ],
  "per_sample_bonus_f1": [
    0.0,
    1.0,
    1.0,
    0.0,
    1.0,
    1.0,
    1.0,
    0.0,
    0.0,
    0.8,
    1.0,
    1.0,
    1.0,
    0.875,
    1.0,
    0.9091,
    1.0,
    0.0,
    1.0,
    0.875,
    1.0,
    0.0,
    0.5,
    0.0,
    1.0,
    0.9643,
    0.9091,
    0.0,
    0.8,
    0.9091,
    1.0,
    1.0,
    1.0,
    0.0,
    0.5,
    0.6667,
    1.0,
    0.0,
    1.0,
    1.0,
    1.0,
    1.0,
    1.0,
    1.0,
    1.0,
    1.0,
    1.0,
    1.0,
    1.0,
    0.8571
  ],
  "error_types": [
    [
      "possible priority/OR-condition interpretation issue (text marker + set difference)",
      19
    ],
    [
      "required/bonus skill mixing",
      18
    ],
    [
      "model-added skills not in human gold",
      17
    ]
  ]
}
```

空学历 gold 以 `null / No explicit education requirement` 参与对比：模型同样未输出学历即为正确，凭空输出学历即为错误（education_compare，08-25）。08-25 起**采集侧 `text_education` 作为教育 hint 投喂**（仅当正文不含学历关键词时追加 `【教育要求】` 行，见 `_jd_text_for_eval`）；比较仅比对 `level`，模型输出 level+major 与 gold 仅 level 视为匹配（major 不参与）。经验按**区间重叠判定**（双 null=命中、单 null=未命中）、核心职责按**词面 containment**（D1-A/D2-A，L1-1 张恺天确认口径，2026-08-20）参与对比。`skills` 与 `requirements[nice]` 分别作为必备技能和加分技能的可观测输出；该映射应在后续算法评审中确认。历史 0.6112 未参与本报告。

## Per-JD results

- oc50_ByteDa_487282604341: title raw=True, normalized=True; skills TP/FP/FN=['AGENT', 'AI辅助编程', 'C++', 'Go', 'Java', '大语言模型', '微服务']/['Linux', '存储', '消息队列', '缓存']/[], F1=0.7778; bonus TP/FP/FN=[]/['AI辅助编程']/['数据库', '消息队列', '缓存'], F1=0.0000; education=True
- oc50_ByteDa_515455338760: title raw=True, normalized=True; skills TP/FP/FN=['C', 'C++', 'Chromium']/['Linux', 'Windows', 'macOS']/[], F1=0.6667; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- oc50_ByteDa_251432053001: title raw=False, normalized=False; skills TP/FP/FN=['Python', '分布式技术', '大语言模型']/['C++', 'Go', 'Java', '检索增强生成']/[], F1=0.6000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- oc50_ByteDa_302650554631: title raw=False, normalized=False; skills TP/FP/FN=['大语言模型', '推荐算法', '机器学习', '自然语言处理']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/['顶会论文（ACL/NeurIPS/ICML/ICLR/CVPR）'], F1=0.0000; education=True
- oc50_ByteDa_192189761801: title raw=False, normalized=False; skills TP/FP/FN=['MLLM', '多模态模型', '大语言模型', '强化学习', '指令微调', '机器学习', '自然语言处理']/[]/[], F1=1.0000; bonus TP/FP/FN=['多模态模型']/[]/[], F1=1.0000; education=False
- oc50_ByteDa_171309177093: title raw=False, normalized=False; skills TP/FP/FN=['C++', 'Continuous Batching', 'DPO', 'DTensor', 'DeviceMesh', 'GRPO', 'Go', 'Java', 'Off-Policy RL', 'OnPolicy Distillation', 'OpenRLHF', 'PPO', 'PagedAttention', 'Post-Training', 'Prefix Caching', 'PyTorch FSDP2', 'Python', 'RL训练', 'Ray', 'Reward Modeling', 'Rust', 'SGLang', 'SRFT', 'VeRL', 'vLLM', '分布式训练', '大模型SFT', '大模型强化学习', '数据结构']/[]/[], F1=1.0000; bonus TP/FP/FN=['OpenRLHF', 'VeRL']/[]/[], F1=1.0000; education=True
- oc50_ByteDa_410460801290: title raw=False, normalized=False; skills TP/FP/FN=['BFF', 'CSS', 'HTML', 'JavaScript', 'React', 'WebIDE', 'Webpack', '高性能表格']/[]/[], F1=1.0000; bonus TP/FP/FN=['React', 'Webpack']/[]/[], F1=1.0000; education=True
- oc50_ByteDa_961238874421: title raw=False, normalized=False; skills TP/FP/FN=['C#', 'C++', 'Git', 'Go', 'JIRA', 'PerDog', 'Perforce', 'Python', 'Renderdoc', 'TAPD', 'UE Insight', 'Unity', 'Unreal', '性能测试', '自动化测试']/['大语言模型']/[], F1=0.9677; bonus TP/FP/FN=[]/['大语言模型']/[], F1=0.0000; education=True
- oc50_ByteDa_150566480141: title raw=False, normalized=False; skills TP/FP/FN=['Apache Flink', 'Apache Spark', 'C++', 'Elasticsearch', 'Hadoop', 'Hive', 'Hudi', 'Iceberg', 'Java', 'Linux', 'OLAP', 'Python', '数据仓库', '数据工程', '特征工程']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/['海量数据处理', '高可用', '高并发'], F1=0.0000; education=True
- oc50_ByteDa_489132472613: title raw=False, normalized=False; skills TP/FP/FN=['AGENT', 'C++', 'Function Calling', 'Go', 'Java', 'Linux', 'Python', 'React', 'Tool Use', 'Vue.js', '分布式技术', '可观测', '大语言模型', '提示工程', '数据库', '检索增强生成', '消息队列', '稳定性治理', '缓存', '高可用', '高并发']/['AIOps', 'DevOps']/[], F1=0.9545; bonus TP/FP/FN=['Claude Code', 'Codex', 'SRE', 'Workflow', '基础设施', '安全治理']/['AIOps', 'DevOps']/['发布'], F1=0.8000; education=True
- oc50_Tencen_334320631808: title raw=False, normalized=False; skills TP/FP/FN=['Apache Kafka', 'Apache Spark', 'C++', 'ClickHouse', 'Go', 'Java', 'MongoDB', 'MySQL', 'RabbitMQ', 'Redis', 'StarRocks']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- oc50_Tencen_283286052864: title raw=False, normalized=False; skills TP/FP/FN=['C++', 'Lua', 'Unreal']/[]/[], F1=1.0000; bonus TP/FP/FN=['3C', 'UE', 'UE5', '主线程', '自研']/[]/[], F1=1.0000; education=True
- oc50_Tencen_639538081792: title raw=False, normalized=False; skills TP/FP/FN=['3D并行', 'AI芯片', 'DPO', 'DeepSpeed', 'GPRO', 'KV Cache', 'MLA', 'Megatron', 'MoE', 'Overlapping', 'PyTorch', 'Transformer', 'VLM', 'ZeRO', 'vLLM', '分布式训练', '大语言模型', '存储', '混合精度', '通信']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- oc50_Tencen_886831570944: title raw=False, normalized=False; skills TP/FP/FN=['C++', 'Go', 'Linux', 'Python', '分布式技术', '数据结构']/[]/[], F1=1.0000; bonus TP/FP/FN=['CXL', 'GPU Direct', 'KVCache', 'PyTorch', 'RDMA', 'SGLang', 'vLLM']/[]/['云原生', '高性能计算'], F1=0.8750; education=False
- oc50_Tencen_595539304448: title raw=False, normalized=False; skills TP/FP/FN=['AI辅助编程', 'C++', 'Python', 'SQL', 'TCP/IP', '大语言模型', '数据分析']/[]/[], F1=1.0000; bonus TP/FP/FN=['SQL', '反爬虫', '自动机对抗', '风控算法']/[]/[], F1=1.0000; education=True
- oc50_Tencen_132004782080: title raw=False, normalized=False; skills TP/FP/FN=['AGENT', 'Go', 'LLM Function Calling', 'MCP', 'Memory', 'Plan-and-Execute', 'Python', 'React', 'Skill', 'Tool Use', '上下文工程', '任务调度', '分布式服务', '基础', '提示工程', '数据结构', '系统设计', '高并发']/[]/[], F1=1.0000; bonus TP/FP/FN=['AI Coding Agent', 'Agent评测体系', 'Bad Case分析', '企业级Agent', '指令微调', '数据分析Agent', '数据建设', '模型评测', '测试Harness', '自动化运维Agent']/['模型应用']/['模型'], F1=0.9091; education=True
- oc50_Tencen_143616069632: title raw=False, normalized=False; skills TP/FP/FN=['AI辅助编程', '小程序']/[]/[], F1=1.0000; bonus TP/FP/FN=['AI相关']/[]/[], F1=1.0000; education=True
- oc50_Tencen_033529323520: title raw=False, normalized=False; skills TP/FP/FN=['C++', 'Go', 'HTTP', 'Java', 'Python', 'TCP/IP', '数据库', '自动化测试']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/['大型电商项目测试'], F1=0.0000; education=True
- oc50_Tencen_187954888704: title raw=False, normalized=False; skills TP/FP/FN=['Apache Flink', 'Apache Spark', 'ClickHouse', 'Doris', 'Hudi', 'Iceberg', 'Impala', 'Presto', 'SQL', '数据pipeline', '数据建模']/[]/[], F1=1.0000; bonus TP/FP/FN=['Hudi', 'Iceberg']/[]/[], F1=1.0000; education=True
- oc50_Tencen_015354896384: title raw=False, normalized=False; skills TP/FP/FN=['BGP', 'CSI', 'Docker', 'ELK', 'GPU Operator', 'Go', 'Grafana', 'Helm', 'Ingress Controller', 'Kubernetes', 'Linux', 'Loki', 'MIG', 'NVIDIA Device Plugin', 'OSPF', 'Prometheus', 'Python', 'Service Mesh', 'Shell', 'TCP/IP', 'VLAN', 'VXLAN', 'containerd']/['Helm Chart', 'Infrastructure as Code']/[], F1=0.9583; bonus TP/FP/FN=['Ansible', 'SaltStack', 'Terraform', 'vGPU', '寒武纪', '昇腾', '海光']/['MIG', '边缘计算']/[], F1=0.8750; education=True
- oc50_Tencen_432884367872: title raw=False, normalized=False; skills TP/FP/FN=['Conformer', 'DeepSpeed', 'Diffusion', 'FastSpeech', 'Megatron', 'PyTorch', 'RNN-T', 'Tacotron2', 'TensorFlow', 'Transformer', 'VITS', '强化学习', '语音合成', '语音识别', '音频生成']/[]/[], F1=1.0000; bonus TP/FP/FN=['多模态模型', '语音大模型', '音频大模型']/[]/[], F1=1.0000; education=True
- oc50_Tencen_854541058048: title raw=False, normalized=False; skills TP/FP/FN=['Kubernetes', 'SRE', 'Serverless', '分布式技术', '变更管控', '多活', '容灾备份', '容量规划', '微服务', '故障演练', '服务网格', '根因分析', '混沌工程', '高可用']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/['决方案/大客户稳定性保障经验'], F1=0.0000; education=True
- oc50_Tencen_726978482688: title raw=False, normalized=False; skills TP/FP/FN=['BRDF', 'Compute Shader', 'Metal', 'OpenGL ES', 'PBR光照', 'Shader', 'UE4', 'UE5', 'Unity', 'Vulkan', '性能分析', '移动GPU', '计算机图形学']/['GPU']/[], F1=0.9630; bonus TP/FP/FN=['3A级手游渲染']/['图形API厂商合作']/['图形API驱动硬件厂商合作'], F1=0.5000; education=True
- oc50_Tencen_756053479424: title raw=False, normalized=False; skills TP/FP/FN=['Apache Kafka', 'C++', 'CDN', 'Go', 'Java', 'Linux', 'MongoDB', 'MySQL', 'Python', 'Redis', 'RocketMQ', '一致性', '分布式KV', '分布式技术', '分库分表', '存储', '微服务网关', '消息队列', '熔断', '缓存', '降级', '限流', '高并发']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/['商/交易等相关业务的后端经验'], F1=0.0000; education=True
- oc50_Tencen_695858184192: title raw=False, normalized=False; skills TP/FP/FN=['Apache Kafka', 'C++', 'CDN', 'Cassandra', 'Elasticsearch', 'Go', 'Java', 'Memcache', 'MongoDB', 'MySQL', 'PostgreSQL', 'Pulsar', 'Python', 'Redis', 'RocketMQ', 'Rust', '分布式技术', '对象存储', '数据结构', '服务治理', '监控告警']/[]/[], F1=1.0000; bonus TP/FP/FN=['CDN', 'UGC', '内容分发', '游戏后台', '社交社区']/[]/[], F1=1.0000; education=True
- oc50_ByteDa_383728597301: title raw=False, normalized=False; skills TP/FP/FN=['A2A', 'Agent工程化', 'Agent编排', 'FaLLBack', 'Go', 'LangChain', 'LangGraph', 'MCP', 'Multi-Agent', 'Prompt', 'Python', 'TTFT', '上下文', '上下文压缩', '云原生', '人工接管', '共享记忆', '分布式技术', '压测', '吞吐', '大语言模型', '工具调用', '异常重试', '效果评测', '模型服务性能', '模型网关', '监控告警', '端到端时延', '结构化输出', '记忆', '错误率', '降级']/[]/[], F1=1.0000; bonus TP/FP/FN=['A2A', 'Agent编排', 'FaLLBack', 'LangChain', 'LangGraph', 'MCP', 'Multi-Agent', 'Prompt', 'TTFT', '上下文', '上下文压缩', '云原生', '人工接管', '共享记忆', '分布式技术', '压测', '吞吐', '工具调用', '异常重试', '效果评测', '模型网关', '监控告警', '端到端时延', '结构化输出', '记忆', '错误率', '降级']/['模型服务性能', '模型服务稳定性']/[], F1=0.9643; education=True
- oc50_ByteDa_249809692981: title raw=False, normalized=False; skills TP/FP/FN=['Go', 'Kubernetes', 'Linux', 'Python', '云原生', '分布式技术', '存储', '容器', '虚拟化']/['Gateway', 'Ingress', '一致性', '容错', '幂等', '服务发现', '重试', '限流', '高可用']/[], F1=0.6667; bonus TP/FP/FN=['CDN', 'Cilium', 'Containerd', 'RTC', 'eBPF']/[]/['直播'], F1=0.9091; education=True
- oc50_Tencen_589012045824: title raw=False, normalized=False; skills TP/FP/FN=['数据分析', '模型建设', '风控算法']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/['良好的应变能力'], F1=0.0000; education=True
- oc50_Tencen_351259385856: title raw=False, normalized=False; skills TP/FP/FN=['AGENT', 'Android', 'C', 'C++', 'Frida', 'HTTP', 'HTTPS', 'IDA', 'Java', 'LLDB', 'Objective-C', 'QUIC', 'TCP/IP', 'Tool Call', 'iOS', '分布式技术', '自动化评测', '调度', '链路追踪', '高并发限流']/['数据平台']/[], F1=0.9756; bonus TP/FP/FN=['Agent Runtime', 'LLM Observability']/[]/['CDN'], F1=0.8000; education=True
- oc50_Tencen_654645460992: title raw=False, normalized=False; skills TP/FP/FN=['Android', 'BN', 'C++', 'Frida', 'Hook', 'IDA', 'LLDB', 'Objective-C', 'iOS', '安全研究', '注入', '越狱', '逆向工程']/[]/[], F1=1.0000; bonus TP/FP/FN=['Kernel', 'XNU', '漏洞分析', '漏洞利用', '漏洞挖掘', '真机自动化', '终端风控', '群控', '设备批量', '设备指纹']/['移动端攻防', '设备平台研发']/[], F1=0.9091; education=False
- oc50_Tencen_732160679936: title raw=False, normalized=False; skills TP/FP/FN=['Pipeline', 'Python', '任务调度', '数据质检', '机器学习']/[]/[], F1=1.0000; bonus TP/FP/FN=['数据质量', '机器学习', '标注', '训练数据']/[]/[], F1=1.0000; education=True
- oc50_Tencen_586997637120: title raw=False, normalized=False; skills TP/FP/FN=['Apache Kafka', 'C', 'C++', 'Go', 'Kubernetes', 'MySQL', 'NoSQL', 'TCP/IP', '分布式技术', '多进程多线程编程', '高可用', '高并发']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- oc50_Tencen_520092016640: title raw=False, normalized=False; skills TP/FP/FN=['多模态模型', '音视频全模态大模型']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=False
- oc50_Tencen_758979194880: title raw=False, normalized=False; skills TP/FP/FN=['C++', 'Metal', 'OpenGL', 'Vulkan', '视频剪辑']/['拍摄', '渲染']/[], F1=0.8333; bonus TP/FP/FN=[]/[]/['研发经验'], F1=0.0000; education=True
- oc50_Tencen_106061701120: title raw=False, normalized=False; skills TP/FP/FN=['C++', 'Lua', 'UE', '动画', '特效']/['关卡', '渲染']/[], F1=0.8333; bonus TP/FP/FN=['AI辅助编程']/[]/['Lua', 'UE'], F1=0.5000; education=True
- oc50_Tencen_026114076672: title raw=False, normalized=False; skills TP/FP/FN=['C++', 'GAS', 'Lua', 'UE', '动画', '特效']/['AI辅助编程', '游戏']/[], F1=0.8571; bonus TP/FP/FN=['AI辅助编程', 'GAS']/['Lua', 'UE']/[], F1=0.6667; education=False
- oc50_Tencen_977711808512: title raw=False, normalized=False; skills TP/FP/FN=['C++', 'Lua', 'UE', '任务']/['游戏任务']/[], F1=0.8889; bonus TP/FP/FN=['Lua', 'UE']/[]/[], F1=1.0000; education=True
- oc50_ByteDa_217591073077: title raw=False, normalized=False; skills TP/FP/FN=['AI', 'DevOps', 'MLOps', '云原生', '机器学习']/['AI应用']/[], F1=0.9091; bonus TP/FP/FN=[]/['AI应用', 'DevOps', 'MLOps', '云原生']/[], F1=0.0000; education=True
- oc50_ByteDa_134817319221: title raw=False, normalized=False; skills TP/FP/FN=['项目管理']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- oc50_ByteDa_355769563445: title raw=False, normalized=False; skills TP/FP/FN=['AGENT', 'SQL']/[]/[], F1=1.0000; bonus TP/FP/FN=['SQL']/[]/[], F1=1.0000; education=True
- oc50_ByteDa_223151900981: title raw=False, normalized=False; skills TP/FP/FN=['SQL', '数据分析']/[]/[], F1=1.0000; bonus TP/FP/FN=['内容审核', '商品审核', '电商治理策略', '策略运营', '规则制定', '风控']/[]/[], F1=1.0000; education=True
- oc50_ByteDa_052867324213: title raw=False, normalized=False; skills TP/FP/FN=['AGENT', 'Coding', 'LLM-as-a-Judge', 'Python', '多轮对话', '大语言模型', '指标', '数据分析', '检索增强生成', '评测Pipeline']/[]/[], F1=1.0000; bonus TP/FP/FN=['DeepEval', 'HumanEval', 'LLM-as-a-Judge', 'LangSmith', 'Langfuse', 'OpenAI Evals', 'RAGAS', 'SWE-bench']/[]/[], F1=1.0000; education=True
- oc50_ByteDa_138068175109: title raw=False, normalized=False; skills TP/FP/FN=['MMORPG', '关卡', '开放世界RPG']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- oc50_ByteDa_870493968693: title raw=False, normalized=False; skills TP/FP/FN=['AI', '产品思维', '数据分析', '流程', '流程诊断', '问题归因']/['AI工具']/[], F1=0.9231; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- oc50_ByteDa_279579138309: title raw=False, normalized=False; skills TP/FP/FN=['Excel', 'Hive', 'MySQL', 'Tableau', '数据分析']/[]/[], F1=1.0000; bonus TP/FP/FN=['商业分析', '用户增长', '营销分析']/[]/[], F1=1.0000; education=True
- oc50_ByteDa_993247480069: title raw=False, normalized=False; skills TP/FP/FN=['数据分析']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- oc50_ByteDa_412718487813: title raw=False, normalized=False; skills TP/FP/FN=['AGENT', 'AIGC', '交互', '视觉']/[]/[], F1=1.0000; bonus TP/FP/FN=['动效']/[]/[], F1=1.0000; education=True
- oc50_ByteDa_249809496373: title raw=False, normalized=False; skills TP/FP/FN=['CDN', 'Go', 'Kubernetes', 'Linux', 'Python', 'RTC', '云原生', '分布式技术', '存储', '容器', '虚拟化']/[]/[], F1=1.0000; bonus TP/FP/FN=['CDN', 'Cilium', 'Containerd', 'RTC', 'eBPF']/[]/[], F1=1.0000; education=True
- oc50_ByteDa_558044100869: title raw=False, normalized=False; skills TP/FP/FN=['世界观设定', '日语', '角色设定']/['游戏世界观设定']/[], F1=0.8571; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- oc50_ByteDa_581849282869: title raw=False, normalized=False; skills TP/FP/FN=['A/B测试', '大语言模型', '提示工程', '数据分析', '检索增强生成', '模型评估', '策略评估']/['AI', '内容分发', '推荐', '策略']/[], F1=0.7778; bonus TP/FP/FN=['AI Workflow', 'Agentic AI', 'Workflow搭建']/[]/['AI'], F1=0.8571; education=True

## Lowest three skill-F1 cases

- oc50_ByteDa_251432053001: skills F1=0.6000; FP=['C++', 'Go', 'Java', '检索增强生成']; FN=[]
- oc50_ByteDa_515455338760: skills F1=0.6667; FP=['Linux', 'Windows', 'macOS']; FN=[]
- oc50_ByteDa_249809692981: skills F1=0.6667; FP=['Gateway', 'Ingress', '一致性', '容错', '幂等', '服务发现', '重试', '限流', '高可用']; FN=[]

## Main automatically classifiable error types

- possible priority/OR-condition interpretation issue (text marker + set difference): 19
- required/bonus skill mixing: 18
- model-added skills not in human gold: 17

Priority/OR conditions are only flagged when a deterministic text marker co-occurs with a set difference; the current schema does not encode condition logic, so these are review candidates rather than conclusive errors.
