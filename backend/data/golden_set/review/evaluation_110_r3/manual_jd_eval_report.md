# A01 人工 JD 集端到端评测报告

本报告只把 `real_llm_success` 行计入指标；`fallback` 和 `failed` 行保留在逐条结果中，但绝不计入指标。

## 当前真实链路

`JDExtractor.extract` → `LLMProviderChain.extract_structured` → `post_process`。岗位名归一化只用于评测侧的 `normalize_position_name` 对照；`PositionAligner`（Neo4j/SBERT）不在 `JDExtractor.extract` 内。

## 三口径说明（08-24 证据链）

`skills_micro_llm_only` = 纯模型输出 vs gold（无补漏、无词面豁免）；`skills_micro_raw` = 模型 + 确定性补漏（gold 词 ∩ 正文词面）；`skills_micro_aligned` = 补漏后 + 词面豁免（PR #330 达标口径）。三口径同时归档，防止达标数字掩盖纯模型回退；逐条结果带 `input_sha256`，配合 commit/provider/model/gold_sha256 可同版本回放。

## 指标

```json
{
  "total_samples": 110,
  "real_llm_success_samples": 110,
  "fallback_samples": 0,
  "failed_samples": 0,
  "title_raw_exact_accuracy": 0.6388888888888888,
  "title_normalized_accuracy": 0.9259259259259259,
  "skills_micro": {
    "tp": 1335,
    "fp": 273,
    "fn": 102,
    "precision": 0.8302238805970149,
    "recall": 0.9290187891440501,
    "f1": 0.8768472906403941
  },
  "skills_micro_aligned": {
    "tp": 1335,
    "fp": 2,
    "fn": 102,
    "precision": 0.9985041136873598,
    "recall": 0.9290187891440501,
    "f1": 0.9625090122566692
  },
  "skills_micro_llm_only": {
    "tp": 1070,
    "fp": 273,
    "fn": 367,
    "precision": 0.7967237527922562,
    "recall": 0.7446068197633959,
    "f1": 0.7697841726618705
  },
  "hallucinated_fp": {
    "ETL": 1,
    "C++": 1
  },
  "provider": "opencode",
  "model": "deepseek-v4-flash",
  "commit": "0d11035",
  "eval_spec_version": "20260824-a",
  "gold_sha256": "ceedfa6987fee665ea53f17678e8f06cb197a632bc828f99c5b962615c508061",
  "skills_average_sample_f1": 0.8853623702734076,
  "bonus_skills_micro": {
    "tp": 75,
    "fp": 52,
    "fn": 31,
    "precision": 0.5905511811023622,
    "recall": 0.7075471698113207,
    "f1": 0.6437768240343347
  },
  "bonus_skills_micro_aligned": {
    "tp": 100,
    "fp": 52,
    "fn": 6,
    "precision": 0.6578947368421053,
    "recall": 0.9433962264150944,
    "f1": 0.7751937984496124
  },
  "bonus_skills_average_sample_f1": 0.7983649683649684,
  "bonus_skills_aligned_average_sample_f1": 0.88011211011211,
  "education_raw_exact_accuracy": 0.9545454545454546,
  "experience_accuracy": 0.7545454545454545,
  "experience_compared": 110,
  "core_duties_micro": {
    "tp": 422,
    "fp": 139,
    "fn": 93,
    "precision": 0.7522281639928698,
    "recall": 0.8194174757281554,
    "f1": 0.7843866171003717
  },
  "per_sample_skills_f1": [
    0.64,
    0.9474,
    0.8,
    0.8148,
    0.9474,
    0.8333,
    0.875,
    1.0,
    0.9474,
    0.9474,
    0.9565,
    0.9091,
    1.0,
    1.0,
    0.931,
    0.9375,
    1.0,
    0.875,
    0.8696,
    1.0,
    1.0,
    0.9231,
    0.8889,
    0.9259,
    0.8276,
    0.9474,
    0.96,
    0.9302,
    0.9565,
    0.9333,
    0.9524,
    0.8,
    0.8511,
    0.6923,
    0.973,
    0.4615,
    1.0,
    0.7273,
    1.0,
    0.8889,
    1.0,
    0.7,
    0.72,
    0.9231,
    0.85,
    0.9167,
    0.75,
    0.7895,
    0.9231,
    0.9333,
    0.9231,
    0.8571,
    0.84,
    0.9697,
    0.96,
    0.85,
    1.0,
    0.4615,
    0.9412,
    0.7317,
    0.6341,
    0.8444,
    0.9286,
    0.8235,
    0.9524,
    0.7692,
    0.8182,
    0.8657,
    0.9231,
    0.9091,
    0.8182,
    1.0,
    0.9655,
    0.8,
    0.8261,
    0.878,
    0.9048,
    1.0,
    0.8519,
    0.9474,
    0.8421,
    0.875,
    1.0,
    0.8,
    0.68,
    0.8667,
    0.6667,
    0.8148,
    0.9143,
    0.8571,
    0.8261,
    0.96,
    0.8333,
    1.0,
    0.96,
    0.8889,
    0.9231,
    0.5882,
    0.9091,
    1.0,
    1.0,
    1.0,
    1.0,
    0.9565,
    1.0,
    0.8571,
    1.0,
    1.0,
    1.0,
    1.0
  ],
  "per_sample_bonus_f1": [
    1.0,
    1.0,
    1.0,
    1.0,
    0.0,
    0.3333,
    0.8,
    1.0,
    1.0,
    0.0,
    0.5,
    0.0,
    1.0,
    0.3333,
    1.0,
    0.5714,
    1.0,
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
    0.6667,
    0.0,
    1.0,
    1.0,
    1.0,
    1.0,
    1.0,
    1.0,
    1.0,
    0.0,
    1.0,
    0.0,
    1.0,
    1.0,
    1.0,
    0.0,
    0.8,
    1.0,
    1.0,
    1.0,
    1.0,
    0.6154,
    1.0,
    0.8,
    0.6667,
    0.3333,
    0.8,
    0.0,
    1.0,
    1.0,
    1.0,
    0.3333,
    0.4,
    0.0,
    0.5,
    1.0,
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
    0.5,
    1.0,
    0.4,
    1.0,
    1.0,
    1.0,
    1.0,
    1.0,
    0.6667,
    1.0,
    1.0,
    0.8,
    1.0,
    1.0,
    1.0,
    1.0,
    0.0,
    1.0,
    1.0,
    1.0,
    0.0,
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
    1.0
  ],
  "error_types": [
    [
      "model-added skills not in human gold",
      77
    ],
    [
      "possible priority/OR-condition interpretation issue (text marker + set difference)",
      57
    ],
    [
      "human-gold skills missed",
      50
    ],
    [
      "skills have both additions and omissions",
      40
    ],
    [
      "title normalization masks a raw-title difference (manual over-normalization check needed)",
      31
    ],
    [
      "required/bonus skill mixing",
      14
    ]
  ]
}
```

空学历 gold 以 `null / No explicit education requirement` 参与对比：模型同样未输出学历即为正确，凭空输出学历即为错误（education_compare，08-25）。08-25 起**采集侧 `text_education` 作为教育 hint 投喂**（仅当正文不含学历关键词时追加 `【教育要求】` 行，见 `_jd_text_for_eval`）；比较仅比对 `level`，模型输出 level+major 与 gold 仅 level 视为匹配（major 不参与）。经验按**区间重叠判定**（双 null=命中、单 null=未命中）、核心职责按**词面 containment**（D1-A/D2-A，L1-1 张恺天确认口径，2026-08-20）参与对比。`skills` 与 `requirements[nice]` 分别作为必备技能和加分技能的可观测输出；该映射应在后续算法评审中确认。历史 0.6112 未参与本报告。

## Per-JD results

- ANN-0001: title raw=True, normalized=True; skills TP/FP/FN=['Apache Spark', 'DolphinScheduler', 'Doris', 'ETL', 'Iceberg', 'Java', 'MinIO', 'SQL']/['Doris SQL', '元数据', '大数据处理', '存算分离', '数据安全', '数据服务', '数据质量', '数据质量监控', '数据集成']/[], F1=0.6400; bonus TP/FP/FN=['Docker', 'Kubernetes']/[]/[], F1=1.0000; education=True
- ANN-0002: title raw=True, normalized=True; skills TP/FP/FN=['CI/CD', 'CSS', 'GitHub Actions', 'HTML', 'JavaScript', 'Jenkins', 'React', 'Vue.js', 'pnpm']/['pnpm workspace']/[], F1=0.9474; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0003: title raw=True, normalized=True; skills TP/FP/FN=['Apache Kafka', 'Ceph', 'Docker', 'Helm', 'Kubernetes', 'Linux', 'Milvus', 'MongoDB', 'MySQL', 'Redis']/['Logging', 'Metrics', 'Tracing', '分布式技术']/['可观测性'], F1=0.8000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0004: title raw=False, normalized=True; skills TP/FP/FN=['AutoGPT', 'LangChain', 'LangGraph', '向量数据库', '多智能体协同', '大模型API', '工具调用', '微服务', '提示工程', '消息队列', '记忆机制']/['OpenAI', '场景解决方案', '架构设计', '百度文心', '阿里通义']/[], F1=0.8148; bonus TP/FP/FN=['Python', '大模型微调', '提示词']/[]/[], F1=1.0000; education=True
- ANN-0005: title raw=True, normalized=True; skills TP/FP/FN=['Apache Spark', 'C', 'C#', 'C++', 'DB2', 'HBase', 'Hadoop', 'Hive', 'Java', 'Linux', 'MapReduce', 'MySQL', 'Oracle', 'Python', 'SQL', 'Storm', 'Unix', '存储过程']/['ETL']/['SQL Server'], F1=0.9474; bonus TP/FP/FN=[]/['C', 'C#', 'C++', 'Java', 'Python']/[], F1=0.0000; education=True
- ANN-0006: title raw=True, normalized=True; skills TP/FP/FN=['Python', '数据分析', '数据处理', '统计分析', '自动驾驶技术栈']/['数据挖掘', '数据格式']/[], F1=0.8333; bonus TP/FP/FN=['数据标注', '质量保障']/['传感器', '感知', '数据制备', '自动驾驶技术栈', '规控', '评测']/['数据评测', '自动驾驶数据制备'], F1=0.3333; education=True
- ANN-0007: title raw=True, normalized=True; skills TP/FP/FN=['CI/CD', 'Java', 'React', 'Spring Boot', 'Spring Cloud', 'Vue.js', '自动化测试']/['AI 编程']/['AI辅助编程'], F1=0.8750; bonus TP/FP/FN=['LIMS', 'MES', 'QMS', '任务调度']/['IT 基础设施']/['IT基础设施'], F1=0.8000; education=True
- ANN-0008: title raw=True, normalized=True; skills TP/FP/FN=['CSS3', 'HTML5', 'JavaScript', 'React', 'React Native', 'Taro', 'TypeScript', 'Vue.js', '前端工程化', '模块化']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=False
- ANN-0009: title raw=True, normalized=True; skills TP/FP/FN=['Airflow', 'Apache Flink', 'Apache Kafka', 'Apache Spark', 'DolphinScheduler', 'ETL', 'HBase', 'HDFS', 'Hadoop', 'Hive SQL', 'Java', 'Python', 'SQL', 'Scala', 'XXL-Job', 'YARN', 'ZooKeeper', '数据仓库建模']/['实时数据处理', '数据倾斜处理']/[], F1=0.9474; bonus TP/FP/FN=['ClickHouse', 'Doris', '数据治理', '高并发数据处理']/[]/[], F1=1.0000; education=True
- ANN-0010: title raw=True, normalized=True; skills TP/FP/FN=['Cocos Creator', 'ECharts', 'React', 'Taro', 'TypeScript', 'Vite', 'Vue.js', 'Webpack', '前端工程化']/['数据可视化']/[], F1=0.9474; bonus TP/FP/FN=[]/[]/['AI对话', '数字人', '智慧大屏'], F1=0.0000; education=True
- ANN-0011: title raw=True, normalized=True; skills TP/FP/FN=['C', 'C++', 'DDD', 'IOC', 'Linux', 'Windows', '内存', '图形性能', '设计模式', '跨平台UI', '面向对象']/[]/['并行性能'], F1=0.9565; bonus TP/FP/FN=['组件式']/['PC端架构']/['视觉软件PC端'], F1=0.5000; education=True
- ANN-0012: title raw=False, normalized=True; skills TP/FP/FN=['C', 'C++', 'Django', 'FFmpeg', 'FastAPI', 'Flask', 'GB28181', 'H.264', 'H.265', 'HTTP/HTTPS', 'MySQL', 'ONVIF', 'PostgreSQL', 'Python', 'RESTful API', 'RTMP', 'RTSP', 'Redis', 'SIP', 'TCP/IP']/['HLS', 'HTTP-FLV', 'WebRTC', '数据结构']/[], F1=0.9091; bonus TP/FP/FN=[]/['Python ONVIF客户端库']/['Python ONVIF', '安防监控', '流媒体对接', '音视频'], F1=0.0000; education=True
- ANN-0013: title raw=True, normalized=True; skills TP/FP/FN=['Apache Spark', 'D3.js', 'ECharts', 'Hadoop', 'Matplotlib', 'Power BI', 'Python', 'Seaborn', 'Tableau', '数据可视化', '概率论', '统计学']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0014: title raw=True, normalized=True; skills TP/FP/FN=['DID', 'Python', 'R', 'SQL', '因果推断', '大数据处理', '工具变量', '数据可视化', '断点回归', '机器学习', '统计学']/[]/[], F1=1.0000; bonus TP/FP/FN=['政策效应评估']/['DID', '因果推断', '工具变量', '断点回归']/[], F1=0.3333; education=True
- ANN-0015: title raw=True, normalized=True; skills TP/FP/FN=['AWS', 'Ansible', 'Azure', 'CI/CD', 'Confluence', 'Datadog', 'Docker', 'Git', 'Grafana', 'ITIL', 'JBoss', 'JIRA', 'Jenkins', 'Linux', 'Maven', 'MongoDB', 'Oracle', 'PostgreSQL', 'Prometheus', 'Puppet', 'Python', 'Ruby', 'Shell', 'Terraform', 'Tomcat', '敏捷方法', '阿里云']/['AWS EC2', 'CloudFormation', 'Route53', 'VPC']/[], F1=0.9310; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0016: title raw=False, normalized=True; skills TP/FP/FN=['Chroma', 'Claude Code', 'Django', 'FastAPI', 'Flask', 'GitHub Copilot', 'Milvus', 'Pinecone', 'Python', '全栈', '向量数据库', '提示工程', '文档解析', '检索增强生成', '知识库工程化']/['切片', '召回']/[], F1=0.9375; bonus TP/FP/FN=['React', 'Vue.js']/['Django', 'FastAPI', 'Flask']/[], F1=0.5714; education=True
- ANN-0017: title raw=False, normalized=True; skills TP/FP/FN=['ArgoCD', 'CI/CD', 'Ceph', 'DevOps', 'GitLab', 'GlusterFS', 'Go', 'Harbor', 'Java', 'Jenkins', 'JuiceFS', 'Kubernetes', 'Linux', 'Python', 'SonarQube', 'TCP/IP', '云原生', '分布式存储', '监控告警', '自动化测试']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0018: title raw=True, normalized=True; skills TP/FP/FN=['AI辅助编程', 'Apache Kafka', 'Claude Code', 'Cursor', 'Java', 'Java Web', 'Redis']/['领域驱动设计']/['领域驱动设计(DDD)'], F1=0.8750; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0019: title raw=True, normalized=True; skills TP/FP/FN=['LSTM', 'Python', 'SVR', '大语言模型', '数据治理', '文本解析', '时序预测', '机器学习', '语义分析', '随机森林']/['AI', '数据分析']/['B端平台全生命周期'], F1=0.8696; bonus TP/FP/FN=[]/[]/['备件智能', '风电备件智能化'], F1=0.0000; education=True
- ANN-0020: title raw=True, normalized=True; skills TP/FP/FN=['Docker', 'Git', 'Pandas', 'Python', 'SQL']/[]/[], F1=1.0000; bonus TP/FP/FN=['自然语言处理']/[]/[], F1=1.0000; education=True
- ANN-0021: title raw=True, normalized=True; skills TP/FP/FN=['DLP', 'EDR', 'IAM', 'PAM', '云平台安全', '安全产品', '最小权限原则']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0022: title raw=True, normalized=True; skills TP/FP/FN=['Java', 'MyBatis', 'MySQL', 'RESTful API', 'SQL', 'Spring Boot']/['数据库']/[], F1=0.9231; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0023: title raw=True, normalized=True; skills TP/FP/FN=['Altium Designer', 'C', 'CPLD', 'FPGA', 'Quartus', 'STM32', 'Verilog', '原理图', '数字电路', '模拟电路', '汇编', '硬件设计']/[]/['51单片机', 'PCB设计', 'Protel 99SE'], F1=0.8889; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0024: title raw=True, normalized=True; skills TP/FP/FN=['Apache Kafka', 'CAP', 'CI/CD', 'DDD', 'Docker', 'Dubbo', 'Elasticsearch', 'IO', 'JVM', 'Java', 'Kubernetes', 'MySQL', 'PostgreSQL', 'Redis', 'RocketMQ', 'Spring Cloud', 'TOGAF', '一致性', '分布式技术', '大语言模型', '并发编程', '微服务', '数据结构', '机器学习', '网络通信']/['SQL', '分库分表', '数据库', '消息队列']/[], F1=0.9259; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0025: title raw=False, normalized=True; skills TP/FP/FN=['CSS3', 'ECharts', 'ElementUI', 'HTML5', 'JavaScript', 'Three.js', 'Vite', 'Vue.js', 'Webpack', '前端工程化', '性能调优', '跨浏览器适配']/['3D图形渲染', '交互', '数据可视化', '用户体验', '组件化']/[], F1=0.8276; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0026: title raw=True, normalized=True; skills TP/FP/FN=['Charles', 'Fiddler', 'JMeter', 'Linux', 'Postman', '功能测试', '接口测试', '测试方法', '测试理论']/['回归测试']/[], F1=0.9474; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0027: title raw=False, normalized=True; skills TP/FP/FN=['BM25', 'Elasticsearch', 'Python', '信息检索', '分词策略', '向量数据库', '向量检索', '提示工程', '文档切片', '检索增强生成', '混合检索', '重排序']/['分词']/[], F1=0.9600; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0028: title raw=False, normalized=True; skills TP/FP/FN=['Adapter', 'BERT', 'C++', 'CI/CD', 'GPT', 'LoRA', 'MindSpore', 'MoE', 'ONNX', 'PyTorch', 'Python', 'TensorFlow', 'TensorRT', 'Transformer', 'vLLM', '推理部署', '检索增强生成', '模型微调', '模型蒸馏', '量化压缩']/['Docker', 'Mindspore', 'ONNX转换']/[], F1=0.9302; bonus TP/FP/FN=['Ascend']/[]/[], F1=1.0000; education=True
- ANN-0029: title raw=True, normalized=True; skills TP/FP/FN=['Echo', 'Gin', 'Go', 'HTTP', 'Linux', 'MySQL', 'Redis', 'Shell', 'gRPC', '并发处理', '微服务']/['问题排查']/[], F1=0.9565; bonus TP/FP/FN=['代理服务', '任务调度']/[]/[], F1=1.0000; education=True
- ANN-0030: title raw=False, normalized=False; skills TP/FP/FN=['CSS', 'ElementUI', 'HTML', 'Java', 'JavaScript', 'MyBatis', 'MySQL', 'Oracle', 'React', 'SQL', 'Spring Boot', 'Spring Cloud', 'Vant', 'Vue.js']/['ECharts']/['数据可视化'], F1=0.9333; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0031: title raw=True, normalized=True; skills TP/FP/FN=['C', 'C++', 'Linux', 'MySQL', 'STL', 'Windows', '多线程', '多进程', '数据结构', '网络编程']/[]/['Visual Studio'], F1=0.9524; bonus TP/FP/FN=['UE']/[]/['仿真系统'], F1=0.6667; education=True
- ANN-0032: title raw=True, normalized=True; skills TP/FP/FN=['C++', 'MATLAB', 'Python', '数据分析', '数据库', '算法部署']/[]/['数据建模', '数据挖掘', '数据清洗'], F1=0.8000; bonus TP/FP/FN=[]/['数据建模', '数据挖掘']/[], F1=0.0000; education=True
- ANN-0033: title raw=True, normalized=True; skills TP/FP/FN=['Dubbo', 'JPA', 'Java', 'MyBatis', 'MySQL', 'OpenGauss', 'Oracle', 'SQL', 'Spring Cloud Alibaba', '分布式技术', '多线程', '并发编程', '消息队列', '缓存', '网络编程', '设计模式', '负载均衡', '达梦数据库', '高并发', '高负载']/['Web系统', 'openGauss', '数据模型', '权限', '消息', '面向对象分析']/['高可用'], F1=0.8511; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0034: title raw=True, normalized=True; skills TP/FP/FN=['信号分析仪', '信号降噪', '声学数据处理', '声学测试分析', '水听器', '水声信号处理', '水声换能器', '示波器', '频谱分析']/['加工工艺', '器件级装配工艺', '声学', '声学材料', '声学测试仪器', '水声传感器件', '水声器件研发', '水声工程']/[], F1=0.6923; bonus TP/FP/FN=['传感器件研发', '声学材料工艺', '水下声学测试']/[]/[], F1=1.0000; education=True
- ANN-0035: title raw=True, normalized=True; skills TP/FP/FN=['', 'Apache Flink', 'Apache Kafka', 'Apache Spark', 'Dubbo', 'Elasticsearch', 'Go', 'Hive', 'Java', 'Python', 'RabbitMQ', 'Redis', 'Spring Cloud', '信用评分', '反欺诈', '微服务', '数据结构', '风控策略']/['特征工程']/[], F1=0.9730; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0036: title raw=False, normalized=True; skills TP/FP/FN=['WBS', '门径', '非标自动化产品']/['多专业协同', '质量', '门径管理流程', '风险控制']/['机电软多专业协同', '特种机器人产品', '项目管理'], F1=0.4615; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0037: title raw=False, normalized=False; skills TP/FP/FN=['React', 'Vite', 'Vue.js', 'Webpack', '兼容性处理', '前端', '前端工程化', '前端性能']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0038: title raw=True, normalized=True; skills TP/FP/FN=['C++', 'Python', '数据分析', '系统设计']/['数据检索', '标签']/['数据挖掘'], F1=0.7273; bonus TP/FP/FN=['数据闭环', '模型训练']/[]/[], F1=1.0000; education=False
- ANN-0039: title raw=True, normalized=True; skills TP/FP/FN=['Linux', 'MySQL', 'Python', 'SQL', '教学能力', '课程']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0040: title raw=False, normalized=True; skills TP/FP/FN=['功能测试', '性能测试', '测试用例', '测试管理']/[]/['银行零售业务'], F1=0.8889; bonus TP/FP/FN=[]/[]/['零售贷款类测试'], F1=0.0000; education=True
- ANN-0041: title raw=True, normalized=True; skills TP/FP/FN=['Ant Design', 'CSS3', 'D3.js', 'ECharts', 'ElementUI', 'Git', 'HTML5', 'HTTP', 'JavaScript', 'Less', 'React', 'SVN', 'Sass', 'TypeScript', 'Vite', 'Vue.js', 'WebSocket', 'Webpack']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0042: title raw=True, normalized=True; skills TP/FP/FN=['交易流程', '功能测试', '接口自动化测试', '测试管理', '自动化测试', '账务逻辑', '软件测试']/['JMeter', 'Postman', '结售汇', '衍生品']/['结售汇业务', '衍生品交易'], F1=0.7000; bonus TP/FP/FN=[]/[]/['结售汇测试'], F1=0.0000; education=True
- ANN-0043: title raw=True, normalized=True; skills TP/FP/FN=['K-Means', 'NumPy', 'Pandas', 'Python', 'SQL', 'scikit-learn', '决策树', '大数据', '数据仓库', '数据分析', '数据清洗', '数据集市', '机器学习', '模型评估', '特征工程', '线性回归', '逻辑回归', '随机森林']/['Apache Spark', 'Hive', 'K-Means聚类', 'Matplotlib', 'PowerBI', 'Seaborn', 'Tableau', '假设检验', '描述性统计', '概率论', '相关性分析', '统计学']/['AI项目流程', '数据可视化'], F1=0.7200; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0044: title raw=True, normalized=True; skills TP/FP/FN=['Doris', 'ETL调度监控', 'Excel', 'MySQL', 'PostgreSQL', 'Power BI', 'SQL', '帆软', '报表系统', '数据分析', '数据可视化', '数据运营']/['ETL']/['SQL性能'], F1=0.9231; bonus TP/FP/FN=['Hive', 'Linux', 'Python']/[]/[], F1=1.0000; education=True
- ANN-0045: title raw=True, normalized=True; skills TP/FP/FN=['ARM', 'C++', 'HTTP', 'IPC', 'JSON', 'Linux', 'Protobuf', 'Rust', 'SQLite', 'Shell', 'Socket', 'TCP', 'UDP', '内存', '多线程', '多进程', '并发安全']/['嵌入式开发', '异步编程', '进程间通信', '高并发']/['并发编程', '异步IO'], F1=0.8500; bonus TP/FP/FN=['IEC104', 'Modbus']/[]/[], F1=1.0000; education=True
- ANN-0046: title raw=True, normalized=True; skills TP/FP/FN=['BI开发', 'CDH', 'CSS', 'Greenplum', 'HTML', 'JavaScript', 'SQL', 'SQL调优', 'TDH', '存储过程', '数据可视化']/['FineBI', 'FineReport']/[], F1=0.9167; bonus TP/FP/FN=[]/[]/['国产大数据'], F1=0.0000; education=True
- ANN-0047: title raw=True, normalized=True; skills TP/FP/FN=['Apifox', 'Linux', 'MySQL', 'Oracle', 'Postman', 'SQL', 'Shell', '接口测试', '测试理论']/['JMeter', '回归测试', '自动化测试', '软件测试']/['测试方法', '测试用例设计'], F1=0.7500; bonus TP/FP/FN=['UI自动化', '接口自动化']/['证券业务流程']/[], F1=0.8000; education=True
- ANN-0048: title raw=False, normalized=True; skills TP/FP/FN=['BPM', 'CI/CD', 'DDD', 'Docker', 'Go', 'Java', 'Kubernetes', 'Rancher', 'Spring Boot', 'Spring Cloud', 'Spring Cloud Alibaba', '分布式技术', '可观测性', '微服务', '高并发']/['API 网关', 'Dubbo', 'Istio', '服务治理', '架构设计', '链路追踪', '领域驱动设计']/['API网关'], F1=0.7895; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0049: title raw=True, normalized=True; skills TP/FP/FN=['数学建模', '数据分析', '数据挖掘', '模型求解', '算法', '运筹']/['编程语言']/[], F1=0.9231; bonus TP/FP/FN=['Python']/[]/[], F1=1.0000; education=True
- ANN-0050: title raw=True, normalized=True; skills TP/FP/FN=['ARM', 'C', 'DSP', 'FPGA', 'STM32', '嵌入式', '控制系统']/['控制']/[], F1=0.9333; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0051: title raw=False, normalized=False; skills TP/FP/FN=['API', 'Agentic AI', 'CI/CD', 'Go', 'Java', 'JavaScript', 'MCP', 'Python', 'TypeScript', '上下文', '任务规划', '前后端分离', '工具调用', '数据库', '日志', '检索增强生成', '消息队列', '缓存']/['Prompt']/['AI辅助编程', 'Web应用'], F1=0.9231; bonus TP/FP/FN=['代码迁移', '低代码', '知识库问答', '研发效能', '自动化办公', '自动化测试']/[]/[], F1=1.0000; education=True
- ANN-0052: title raw=False, normalized=False; skills TP/FP/FN=['Docker', 'FastAPI', 'Flask', 'FunctionCalling', 'JWT', 'Memory', 'MySQL', 'PyMySQL', 'Python', 'RESTful API', 'React', 'SQLAlchemy', 'SSE', 'ToolCalling', '流式输出']/['Dockerfile', 'docker-compose']/['Agentic AI', 'LLM API', 'OpenAI SDK'], F1=0.8571; bonus TP/FP/FN=['MinIO', 'S3', '提示工程', '检索增强生成']/['Agentic AI', 'React', 'SSE', 'ToolCalling']/['多轮对话'], F1=0.6154; education=True
- ANN-0053: title raw=False, normalized=True; skills TP/FP/FN=['BurpSuite', 'Cknife', 'IPS', 'Kali', 'Linux', 'Nmap', 'PKI/CA', 'Python', 'Shell', 'Sqlmap', 'VPN', 'WinHex', 'Wireshark', 'Xsser', '入侵检测', '安全', '应用安全', '渗透测试', '漏洞扫描', '系统加固', '防火墙']/['安全监测', '攻击溯源', '漏扫', '漏洞整改', '网络安全', '网络安全情报', '网络攻击', '自动化脚本']/[], F1=0.8400; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0054: title raw=False, normalized=True; skills TP/FP/FN=['APS', 'C++', 'CV', 'PyTorch', 'Python', 'TensorFlow', '句法分析', '大语言模型', '情感分析', '机器学习', '模型训练', '深度学习', '特征提取', '自然语言处理', '词法分析', '语义理解']/['计算机视觉']/[], F1=0.9697; bonus TP/FP/FN=['优化', '运筹学']/['APS']/[], F1=0.8000; education=True
- ANN-0055: title raw=False, normalized=True; skills TP/FP/FN=['C', 'C++', 'ETL', 'Java', 'Linux', 'Python', '协同过滤', '场景', '大数据', '推荐', '推荐算法', '用户画像']/['大数据平台']/[], F1=0.9600; bonus TP/FP/FN=['DMP', '用户画像建模']/['推荐', '用户画像']/[], F1=0.6667; education=True
- ANN-0056: title raw=False, normalized=True; skills TP/FP/FN=['AI模型聚合', 'API网关', 'Docker', 'Gin', 'Go', 'Kubernetes', 'MySQL', 'NewAPI', 'OpenAI', 'Redis', '分布式技术', '流式传输', '消息队列', '熔断降级', '负载均衡', '限流', '高并发']/['OpenAI兼容', '云原生', '分层架构', '大模型API', '限流风控']/['大模型API对接'], F1=0.8500; bonus TP/FP/FN=['支付计费']/['API网关', 'NewAPI', '大语言模型']/['商业化API'], F1=0.3333; education=True
- ANN-0057: title raw=True, normalized=True; skills TP/FP/FN=['', '召回', '推荐', '数据结构', '机器学习', '深度学习', '混排', '粗排', '精排']/[]/[], F1=1.0000; bonus TP/FP/FN=['搜索引擎', '计算广告']/[]/['大规模推荐'], F1=0.8000; education=True
- ANN-0058: title raw=False, normalized=False; skills TP/FP/FN=['优化', '数据挖掘', '机器学习']/['代数', '概率', '统计']/['强化学习', '数学建模', '概率统计', '深度学习'], F1=0.4615; bonus TP/FP/FN=[]/['数理统计', '机制', '运筹']/[], F1=0.0000; education=True
- ANN-0059: title raw=True, normalized=True; skills TP/FP/FN=['信息安全', '公文写作', '安全培训', '安全检查', '安全风险排查', '应急', '数据安全', '等级保护']/[]/['安全审计'], F1=0.9412; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0060: title raw=True, normalized=True; skills TP/FP/FN=['CSS', 'Git', 'HTML', 'Java', 'JavaScript', 'MySQL', 'Oracle', 'PostgreSQL', 'RESTful API', 'React', 'Spring Boot', 'TypeScript', '性能调优', '接口设计', '数据库']/['Agentic AI', 'LLM API', '事务', '幂等性', '并发', '异常处理', '智能问答', '检索增强生成', '缓存']/['AI能力集成', '后端工程基础'], F1=0.7317; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0061: title raw=False, normalized=True; skills TP/FP/FN=['BSP', 'C', 'C++', '代码审查', '传感器数据融合', '嵌入式编程', '模型导出', '模型轻量化', '深度学习', '硬件调试', '系统调试', '通讯协议', '驱动']/['PyTorch', 'TensorFlow', '传感器融合', '剪枝', '模型导出与转换', '系统调优', '蒸馏', '通讯']/['AI模型部署', '模型剪枝', '模型蒸馏', '模型转换', '模型量化', '系统架构', '边缘部署'], F1=0.6341; bonus TP/FP/FN=['交换机', '硬件', '视频']/[]/[], F1=1.0000; education=True
- ANN-0062: title raw=False, normalized=True; skills TP/FP/FN=['CI/CD', 'DevOps', 'DevOps工具链', 'Django', 'Flask', 'Go', 'Linux', 'Python', 'Shell', 'TCP/IP', '公有云', '安全策略', '性能分析', '数据备份', '日志分析', '漏洞修复', '监控告警', '系统巡检', '自动化']/['Ansible', 'Git', 'Jenkins', '腾讯云', '阿里云']/['Docker', '容灾恢复'], F1=0.8444; bonus TP/FP/FN=['CMDB']/['发布', '自动化运维']/['持续集成平台', '自动化运维平台'], F1=0.3333; education=False
- ANN-0063: title raw=False, normalized=False; skills TP/FP/FN=['OpenCV', 'PyTorch', 'Python', 'TensorFlow', '数字信号处理', '数据预处理', '机器学习', '模型', '深度学习', '特征工程', '算法工程化', '视频融合', '计算机视觉']/['TensorRT']/['模型推理'], F1=0.9286; bonus TP/FP/FN=['TensorRT']/['智能巡检', '视频分析', '预测性维护']/[], F1=0.4000; education=True
- ANN-0064: title raw=False, normalized=True; skills TP/FP/FN=['ARM Cortex-M', 'C', 'C++', 'CAN', 'Ethernet', 'FreeRTOS', 'I2C', 'MQTT', 'OTA', 'RISC-V', 'RTOS', 'SPI', 'TCP/IP', 'UART', 'USB', '传感器融合', '原理图', '固件', '嵌入式开发', '插补', '数字电路', '模拟电路', '步进电机控制', '硬件调试', '路径规划', '运动学模型', '运动控制', '驱动']/['3D 打印', 'G 代码解析', 'Klipper', 'Linux 内核', 'Marlin', 'PID 控制', '信号完整性分析', '数据结构', '电源']/['G代码解析', 'Linux内核', 'PID控制'], F1=0.8235; bonus TP/FP/FN=[]/[]/['3D打印控制系统'], F1=0.0000; education=True
- ANN-0065: title raw=True, normalized=True; skills TP/FP/FN=['ElementUI', 'Python', 'Vue.js', '全栈', '前端', '后端', '微服务', '性能调优', '故障处理', '数据库']/[]/['RESTful API'], F1=0.9524; bonus TP/FP/FN=['数据可视化']/['AI']/['AI应用'], F1=0.5000; education=True
- ANN-0066: title raw=True, normalized=True; skills TP/FP/FN=['AI工作流', 'AI应用', 'AI模型调用', 'Elasticsearch', 'Go', 'MCP', 'Python', '接口', '数据安全合规', '数据治理', '数据质量管控', '数据资产', '数据采集', '系统架构', '跨系统集成']/['AI模型', 'Skill', '数据资产梳理', '需求分析']/['AI模型微调', 'AI模型部署', '数据整合', '数据标准化', '数据清洗'], F1=0.7692; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0067: title raw=False, normalized=True; skills TP/FP/FN=['AI辅助编程', 'CI/CD', 'CNCF', 'DevOps', 'Docker', 'HTTPS', 'IaC', 'Kubernetes', 'OAuth2', '云原生', '云安全', '凭证', '多云', '多租户', '容器化', '微服务', '证书', '高可用']/['Azure', '云原生设计模式', '基础设施即代码', '阿里云', '阿里金融云']/['Kubernetes存储', 'Kubernetes网络', '公有云'], F1=0.8182; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0068: title raw=False, normalized=True; skills TP/FP/FN=['ARM', 'BSP', 'C', 'C++', 'DRM', 'DTS', 'H.264', 'H.265', 'HDMI', 'HTTP', 'I2C', 'I2S', 'ISP', 'Linux驱动', 'MIPI-CSI', 'MJPEG', 'Makefile', 'Python', 'RTMP', 'RTSP', 'SPI', 'Shell', 'UART', 'USB', 'V4L2', '平台设备驱动', '芯片功能验证', '设备树', '音视频编解码']/['Audio', 'Camera', 'LCD', 'Linux', 'VPU', 'WiFi', '设备驱动']/['Camera驱动', '字符设备驱动'], F1=0.8657; bonus TP/FP/FN=[]/['RK']/[], F1=0.0000; education=True
- ANN-0069: title raw=False, normalized=False; skills TP/FP/FN=['', 'Go', 'Linux', 'MySQL', 'Redis', '分布式技术', '可扩展', '数据结构', '服务化', '消息队列', '系统架构', '高可用']/[]/['代码审查', '监控预警'], F1=0.9231; bonus TP/FP/FN=['大模型应用']/[]/[], F1=1.0000; education=True
- ANN-0070: title raw=False, normalized=False; skills TP/FP/FN=['', 'CSS', 'HTML', 'Java', 'JavaScript', 'Spring Boot', 'Web开发', '数据结构', '系统设计', '网络编程']/['C++']/['性能调优'], F1=0.9091; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0071: title raw=False, normalized=True; skills TP/FP/FN=['', 'Benchmark', 'CUDA', 'CV', 'Docker', 'Linux', 'LoRA', 'PyTorch', 'Python', 'QLoRA', 'TensorFlow', 'Transformer', '大语言模型', '推理加速', '数据结构', '模型', '模型量化', '自然语言处理']/['DeepSeek', 'Qwen-VL', '多模态 AI', '数学基础']/['AI评测体系', '多模态AI', '数据构建', '模型微调'], F1=0.8182; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0072: title raw=True, normalized=True; skills TP/FP/FN=['Angular', 'CI/CD', 'Django', 'Docker', 'Express.js', 'Java', 'JavaScript', 'Kubernetes', 'Linux', 'MongoDB', 'MySQL', 'Nginx', 'Node.js', 'PostgreSQL', 'Python', 'RESTful API', 'React', 'Redis', 'Spring Boot', 'Vue.js', 'Webpack', '实时通信', '数据安全', '高并发']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0073: title raw=False, normalized=True; skills TP/FP/FN=['C', 'C++', 'EMC', 'PCB', '以太网', '单片机', '单片机操作', '原理图', '器件选型', '嵌入式开发', '应用层', '底层驱动', '硬件设计', '驱动']/[]/['硬件调试'], F1=0.9655; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0074: title raw=True, normalized=True; skills TP/FP/FN=['Web安全', '公有云安全', '合规审计', '密保测评', '漏洞扫描', '病毒木马防范', '程序漏洞检测', '终端安全', '角色权限', '访问控制', '身份认证', '防DDOS']/['WEB安全', '网络安全法律法规']/['安全治理', '安全管理体系', '等保测评', '网络安全规划'], F1=0.8000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0075: title raw=False, normalized=True; skills TP/FP/FN=['Apache Flink', 'Apache Spark', 'Doris', 'ETL', 'HBase', 'Hadoop', 'JVM', 'Java', 'MyBatis', 'Ranger', 'SQL', 'Spring Boot', '分布式技术', '多线程', '数据仓库', '数据采集', '服务化', '维度建模', '网络编程']/['BI', '元数据', '分层模型', '组件化']/['JVM调优', 'SQL性能', '数据安全', '数据治理'], F1=0.8261; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0076: title raw=True, normalized=True; skills TP/FP/FN=['ARM', 'C', 'C++', 'CAN', 'I2C', 'Linux', 'MCU', 'RS-232', 'RS-485', 'RTOS', 'SPI', 'TCP/IP', 'UART', 'UDP', '传感器', '嵌入式开发', '电机控制', '驱动']/['STM32', '裸机', '软硬件']/['硬件调试', '软硬件联调'], F1=0.8780; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0077: title raw=False, normalized=True; skills TP/FP/FN=['AIGC', 'RL', '世界模型', '偏好对齐', '多模态CoT', '多模态理解', '强化学习', '指令微调', '数据合成', '数据建设', '智能Agent', '机器学习', '模型推理', '物理保真生成', '物理渲染', '自然语言处理', '视频生成', '计算机视觉', '评测体系']/['多模态模型', '多模态理解与生成']/['图像生成', '多模态生成'], F1=0.9048; bonus TP/FP/FN=['C++', 'Python']/['基础', '机器学习', '自然语言处理']/['C'], F1=0.5000; education=True
- ANN-0078: title raw=False, normalized=False; skills TP/FP/FN=['CSS', 'HTML', 'JavaScript', 'React', 'Vue.js', '兼容性测试', '前端', '性能调优', '接口联调', '组件库']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0079: title raw=False, normalized=True; skills TP/FP/FN=['Agentic AI', 'Apache Flink', 'Apache Kafka', 'Apache Spark', 'CV', 'ETL', 'FastAPI', 'Hadoop', 'Hive', 'Java', 'Linux', 'MLOps', 'PyTorch', 'Python', 'SQL', 'TensorFlow', '多模态交互', '大语言模型', '数据仓库', '机器学习', '检索增强生成', '深度学习', '自然语言处理']/['数据仓库建模', '查询', '计算机视觉']/['数据存储', '数据建模', '数据清洗', '数据计算', '数据采集'], F1=0.8519; bonus TP/FP/FN=['向量数据库']/[]/['LoRA', 'QLoRA', '模型推理部署'], F1=0.4000; education=True
- ANN-0080: title raw=True, normalized=True; skills TP/FP/FN=['Apache', 'CSS3', 'Django', 'Express.js', 'Flask', 'HTML5', 'JavaScript', 'Laravel', 'Linux', 'MySQL', 'NestJS', 'Nginx', 'Node.js', 'PHP', 'PostgreSQL', 'Python', 'React', 'Vue.js']/[]/['域名配置', '性能调优'], F1=0.9474; bonus TP/FP/FN=['Docker']/[]/[], F1=1.0000; education=True
- ANN-0081: title raw=True, normalized=True; skills TP/FP/FN=['Python', '大语言模型', '强化学习', '数据并行', '机器学习', '模型并行', '深度学习', '迁移学习']/['PyTorch', 'TensorFlow']/['模型微调'], F1=0.8421; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0082: title raw=False, normalized=True; skills TP/FP/FN=['C', 'C++', 'Linux', 'RTOS', 'SMP', '中断', '任务调度', '内存', '多核编程', '存储', '嵌入式', '线程', '设计模式', '面向对象']/['功耗', '硬件', '系统启动流程', '系统调试']/[], F1=0.8750; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0083: title raw=False, normalized=False; skills TP/FP/FN=['', 'C', 'C++', 'Go', 'Java', 'Python', '数据结构', '设计模式']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0084: title raw=True, normalized=True; skills TP/FP/FN=['C', 'C++', '传感器', '信号处理', '单片机', '嵌入式开发', '底层', '硬件驱动']/['AI', '传感器原理', '传感器选型', '抗干扰']/[], F1=0.8000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0085: title raw=True, normalized=True; skills TP/FP/FN=['Ansible', 'BGP', 'CDN', 'CI/CD', 'DNS', 'Docker', 'ELK', 'Grafana', 'HTTP', 'Kubernetes', 'Linux', 'Prometheus', 'Python', 'Shell', 'TCP/IP', 'TLS', 'Terraform']/['AWS', 'Aliyun', 'CloudWatch', 'EKS', 'GitHub Actions', 'GitLab CI', 'IAM', 'Jenkins', 'KMS', 'OCI Monitoring', 'Oracle Cloud', 'Secrets Manager', 'VCN', 'VPC', 'Vault']/['公有云'], F1=0.6800; bonus TP/FP/FN=['Service Mesh']/['EKS']/[], F1=0.6667; education=True
- ANN-0086: title raw=True, normalized=True; skills TP/FP/FN=['AI辅助编程', 'CSS3', 'Element UI', 'HTML5', 'Java', 'JavaScript', 'MySQL', 'Redis', 'SQL', 'Spring Boot', 'TypeScript', 'Vue.js', 'uni-app']/['Flex', 'Grid', 'Uniapp UI', '响应式']/[], F1=0.8667; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0087: title raw=False, normalized=True; skills TP/FP/FN=['Java', 'Linux', '自动化运维']/['Ansible', 'Shell']/['运维脚本'], F1=0.6667; bonus TP/FP/FN=['Docker', 'Kubernetes']/[]/[], F1=1.0000; education=True
- ANN-0088: title raw=True, normalized=True; skills TP/FP/FN=['Angular', 'Apache Spark', 'CSS3', 'Django', 'Flask', 'Go', 'HTML5', 'Hadoop', 'Java', 'JavaScript', 'MongoDB', 'MySQL', 'Node.js', 'PostgreSQL', 'PyTorch', 'Python', 'React', 'Redis', 'Spring Boot', 'TensorFlow', 'TypeScript', 'Vue.js']/['AWS', 'Docker', 'Kubernetes', '云原生', '多模态数据处理', '大模型训练', '数据仓库', '腾讯云', '阿里云']/['公有云'], F1=0.8148; bonus TP/FP/FN=['Docker', 'Kubernetes']/['多模态数据处理']/[], F1=0.8000; education=True
- ANN-0089: title raw=False, normalized=True; skills TP/FP/FN=['ARM', 'BACnet', 'C', 'C++', 'CAN', 'FreeRTOS', 'HTTP', 'LoRaWAN', 'MQTT', 'Modbus', 'OPC UA', 'RS485', 'RT-Thread', 'STM32', 'TCP/IP', '嵌入式开发']/['BACnet IP', 'MCU', 'X86']/[], F1=0.9143; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0090: title raw=True, normalized=True; skills TP/FP/FN=['CoAP', 'Docker', 'HTTP', 'Java', 'Kubernetes', 'MQTT', 'MongoDB', 'MySQL', 'Redis', 'Spring Boot', 'Spring Cloud', 'TCP', 'UDP', '微服务', '高并发']/['云原生', '分布式技术', '服务熔断', '限流', '高可用']/[], F1=0.8571; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0091: title raw=True, normalized=True; skills TP/FP/FN=['AI辅助编程', 'Ant Design Vue', 'Axios', 'CSS3', 'ElementUI', 'Git', 'HTML5', 'Java', 'JavaScript', 'MyBatis', 'MySQL', 'Oracle', 'Pinia', 'RESTful API', 'Spring Boot', 'Spring MVC', 'Vite', 'Vue.js', 'Vuex']/['Flex', 'Grid', 'JVM', 'SQL', '数据库建模']/['Element UI', 'Vue Router', '提示词工程'], F1=0.8261; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=False
- ANN-0092: title raw=False, normalized=True; skills TP/FP/FN=['Docker', 'Dubbo', 'IO', 'Java', 'MyBatis', 'NIO', 'RocketMQ', 'Spring Boot', '分布式技术', '多线程', '设计模式', '通信']/['分布式服务']/[], F1=0.9600; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0093: title raw=True, normalized=True; skills TP/FP/FN=['Apache Kafka', 'Java', 'MySQL', 'RabbitMQ', 'Redis', 'Spring Boot', 'Spring Cloud', '分布式技术', '接口安全', '风控']/['接口设计', '数据库', '系统分层', '高并发']/[], F1=0.8333; bonus TP/FP/FN=[]/['接口安全']/[], F1=0.0000; education=True
- ANN-0094: title raw=True, normalized=True; skills TP/FP/FN=['Java', 'MyBatis', 'MySQL', 'RabbitMQ', 'Redis', 'Spring Boot', '微服务']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0095: title raw=False, normalized=True; skills TP/FP/FN=['API', 'CSS3', 'Go', 'HTML5', 'Java', 'JavaScript', 'Node.js', 'React', 'TypeScript', 'Vue.js', '微服务', '数据库']/['前后端分离']/[], F1=0.9600; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0096: title raw=True, normalized=True; skills TP/FP/FN=['Java', 'MyBatis', 'MySQL', 'Spring Boot']/['SQL']/[], F1=0.8889; bonus TP/FP/FN=['微服务', '消息队列']/[]/[], F1=1.0000; education=True
- ANN-0097: title raw=False, normalized=True; skills TP/FP/FN=['Apache Kafka', 'Go', 'Go-Zero', 'Java', 'Linux', 'MySQL', 'PHP', 'RTC', 'Redis', 'RocketMQ', 'Spring Boot', 'WebSocket']/['分布式技术', '高并发']/[], F1=0.9231; bonus TP/FP/FN=[]/[]/['IM/直播SDK', '分布式技术', '第三方支付对接'], F1=0.0000; education=True
- ANN-0098: title raw=True, normalized=True; skills TP/FP/FN=['', 'NoSQL数据库', 'Python', 'Web', '数据结构']/['Django', 'Flask', 'MySQL', 'NoSQL', 'Redis', 'SQL']/['SQL数据库'], F1=0.5882; bonus TP/FP/FN=[]/['工业数据处理', '物联网']/[], F1=0.0000; education=True
- ANN-0099: title raw=True, normalized=True; skills TP/FP/FN=['Java', 'MySQL', 'Redis', 'Spring Boot', 'Spring Cloud']/['SQL']/[], F1=0.9091; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0100: title raw=True, normalized=True; skills TP/FP/FN=['Java', 'MySQL', 'Redis', 'Spring Boot', 'Spring Cloud', '微服务']/[]/[], F1=1.0000; bonus TP/FP/FN=['分布式技术']/[]/[], F1=1.0000; education=True
- ANN-0101: title raw=True, normalized=True; skills TP/FP/FN=['Go', 'Java', 'Python', '后端', '数据库']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=False
- ANN-0102: title raw=False, normalized=True; skills TP/FP/FN=['CSS', 'HTML', 'Java', 'JavaScript', 'MySQL', 'Node.js', 'Python', 'React', 'Vue.js']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0103: title raw=True, normalized=True; skills TP/FP/FN=['Java', 'MyBatis', 'MySQL', 'RESTful API', 'Spring Boot']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0104: title raw=True, normalized=True; skills TP/FP/FN=['CSS3', 'HTML5', 'Java', 'JavaScript', 'Node.js', 'Python', 'React', 'Vue.js', '响应式', '数据库', '跨端适配']/['数据可视化']/[], F1=0.9565; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0105: title raw=True, normalized=True; skills TP/FP/FN=['Java', 'MyBatis', 'MySQL', 'RESTful API', 'SQL', 'Spring Boot']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0106: title raw=True, normalized=True; skills TP/FP/FN=['CSS3', 'HTML5', 'JavaScript', 'MySQL', 'PHP', '数据库']/['Laravel', 'ThinkPHP']/[], F1=0.8571; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0107: title raw=True, normalized=True; skills TP/FP/FN=['Java', 'JavaScript', 'MySQL', 'Python', 'Redis', 'Spring Boot']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0108: title raw=True, normalized=True; skills TP/FP/FN=['Java', 'MyBatis', 'MySQL', 'Spring Boot', '数据结构']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0109: title raw=True, normalized=True; skills TP/FP/FN=['Java', 'MySQL', 'Redis', 'Spring Boot', 'Spring Cloud', '数据库']/[]/[], F1=1.0000; bonus TP/FP/FN=['分布式技术', '高并发']/[]/[], F1=1.0000; education=True
- ANN-0110: title raw=True, normalized=True; skills TP/FP/FN=['Java', 'MyBatis', 'MySQL', 'SQL', 'Spring Boot']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True

## Lowest three skill-F1 cases

- ANN-0036: skills F1=0.4615; FP=['多专业协同', '质量', '门径管理流程', '风险控制']; FN=['机电软多专业协同', '特种机器人产品', '项目管理']
- ANN-0058: skills F1=0.4615; FP=['代数', '概率', '统计']; FN=['强化学习', '数学建模', '概率统计', '深度学习']
- ANN-0098: skills F1=0.5882; FP=['Django', 'Flask', 'MySQL', 'NoSQL', 'Redis', 'SQL']; FN=['SQL数据库']

## Main automatically classifiable error types

- model-added skills not in human gold: 77
- possible priority/OR-condition interpretation issue (text marker + set difference): 57
- human-gold skills missed: 50
- skills have both additions and omissions: 40
- title normalization masks a raw-title difference (manual over-normalization check needed): 31
- required/bonus skill mixing: 14

Priority/OR conditions are only flagged when a deterministic text marker co-occurs with a set difference; the current schema does not encode condition logic, so these are review candidates rather than conclusive errors.
