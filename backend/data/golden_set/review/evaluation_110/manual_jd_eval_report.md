# A01 人工 JD 集端到端评测报告

本报告只把 `real_llm_success` 行计入指标；`fallback` 和 `failed` 行保留在逐条结果中，但绝不计入指标。

## 当前真实链路

`JDExtractor.extract` → `LLMProviderChain.extract_structured` → `post_process`。岗位名归一化只用于评测侧的 `normalize_position_name` 对照；`PositionAligner`（Neo4j/SBERT）不在 `JDExtractor.extract` 内。

## 指标

```json
{
  "total_samples": 110,
  "real_llm_success_samples": 108,
  "fallback_samples": 2,
  "failed_samples": 0,
  "title_raw_exact_accuracy": 0.6203703703703703,
  "title_normalized_accuracy": 0.9259259259259259,
  "skills_micro": {
    "tp": 1310,
    "fp": 249,
    "fn": 100,
    "precision": 0.8402822322001283,
    "recall": 0.9290780141843972,
    "f1": 0.8824520040417649
  },
  "skills_micro_aligned": {
    "tp": 1310,
    "fp": 1,
    "fn": 100,
    "precision": 0.9992372234935164,
    "recall": 0.9290780141843972,
    "f1": 0.9628812936420433
  },
  "hallucinated_fp": {
    "ETL": 1
  },
  "skills_average_sample_f1": 0.8891886960516077,
  "bonus_skills_micro": {
    "tp": 64,
    "fp": 60,
    "fn": 36,
    "precision": 0.5161290322580645,
    "recall": 0.64,
    "f1": 0.5714285714285714
  },
  "bonus_skills_average_sample_f1": 0.7718547912992356,
  "education_raw_exact_accuracy": 0.7592592592592593,
  "experience_accuracy": 0.8888888888888888,
  "experience_compared": 108,
  "core_duties_micro": {
    "tp": 129,
    "fp": 416,
    "fn": 376,
    "precision": 0.23669724770642203,
    "recall": 0.25544554455445545,
    "f1": 0.24571428571428572
  },
  "per_sample_skills_f1": [
    0.5714,
    0.9474,
    0.8696,
    0.88,
    0.9474,
    0.9091,
    0.9333,
    1.0,
    0.878,
    0.9474,
    0.9167,
    0.9302,
    0.96,
    0.9167,
    0.931,
    0.8824,
    1.0,
    0.9091,
    0.9091,
    1.0,
    1.0,
    0.8571,
    0.963,
    0.9231,
    1.0,
    0.8,
    0.9091,
    1.0,
    0.9333,
    0.9091,
    0.8,
    0.8889,
    0.9,
    0.9231,
    0.6,
    1.0,
    0.8889,
    1.0,
    0.8889,
    1.0,
    0.7778,
    0.8,
    0.8276,
    0.7907,
    0.88,
    0.7826,
    0.8824,
    0.9231,
    0.875,
    0.8571,
    0.875,
    0.9143,
    0.96,
    0.8718,
    1.0,
    0.375,
    0.9412,
    0.8571,
    0.7027,
    0.8444,
    0.8966,
    0.8235,
    0.9091,
    0.7895,
    0.9,
    0.8529,
    0.8889,
    0.9524,
    0.9,
    0.9796,
    0.9655,
    0.8,
    0.8636,
    0.9,
    0.9048,
    1.0,
    0.8846,
    0.9474,
    0.8,
    0.9655,
    1.0,
    0.9412,
    0.6538,
    0.8667,
    0.5455,
    0.8148,
    0.9412,
    0.8824,
    0.7308,
    0.9167,
    0.8696,
    1.0,
    0.96,
    0.8,
    0.9231,
    0.5556,
    0.9091,
    0.9231,
    1.0,
    1.0,
    1.0,
    1.0,
    1.0,
    0.8571,
    1.0,
    1.0,
    0.8571,
    1.0
  ],
  "per_sample_bonus_f1": [
    1.0,
    1.0,
    1.0,
    0.8,
    1.0,
    0.5,
    1.0,
    1.0,
    0.6667,
    1.0,
    0.6667,
    0.0,
    1.0,
    0.0,
    1.0,
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
    1.0,
    1.0,
    0.0,
    1.0,
    1.0,
    1.0,
    1.0,
    1.0,
    0.0,
    1.0,
    1.0,
    1.0,
    1.0,
    1.0,
    0.2222,
    0.4,
    1.0,
    0.6667,
    0.0,
    0.5714,
    1.0,
    1.0,
    0.6667,
    0.6667,
    0.0,
    1.0,
    1.0,
    1.0,
    1.0,
    0.0,
    0.6667,
    0.0,
    0.6667,
    0.0,
    1.0,
    1.0,
    0.0,
    1.0,
    1.0,
    1.0,
    1.0,
    1.0,
    1.0,
    0.0,
    0.4,
    0.0,
    0.6667,
    1.0,
    0.0,
    1.0,
    1.0,
    1.0,
    1.0,
    1.0,
    1.0,
    0.8,
    1.0,
    0.0,
    1.0,
    1.0,
    0.0,
    1.0,
    1.0,
    0.6667,
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
      78
    ],
    [
      "possible priority/OR-condition interpretation issue (text marker + set difference)",
      60
    ],
    [
      "human-gold skills missed",
      49
    ],
    [
      "skills have both additions and omissions",
      40
    ],
    [
      "title normalization masks a raw-title difference (manual over-normalization check needed)",
      33
    ],
    [
      "required/bonus skill mixing",
      29
    ]
  ]
}
```

空学历 gold 以 `null / No explicit education requirement` 参与 raw exact 对比：模型同样未输出学历即为正确，凭空输出学历即为错误。经验按**区间重叠判定**（双 null=命中、单 null=未命中）、核心职责按**词面 containment**（D1-A/D2-A，L1-1 张恺天确认口径，2026-08-20）参与对比。`skills` 与 `requirements[nice]` 分别作为必备技能和加分技能的可观测输出；该映射应在后续算法评审中确认。历史 0.6112 未参与本报告。

## Per-JD results

- ANN-0001: title raw=True, normalized=True; skills TP/FP/FN=['Apache Spark', 'DolphinScheduler', 'Doris', 'ETL', 'Iceberg', 'Java', 'MinIO', 'SQL']/['Docker', 'Doris SQL', 'Kubernetes', '元数据', '存算分离', '数据加工', '数据安全', '数据服务', '数据校验', '数据清洗', '数据质量', '数据集成']/[], F1=0.5714; bonus TP/FP/FN=['Docker', 'Kubernetes']/[]/[], F1=1.0000; education=True
- ANN-0002: title raw=True, normalized=True; skills TP/FP/FN=['CI/CD', 'CSS', 'GitHub Actions', 'HTML', 'JavaScript', 'Jenkins', 'React', 'Vue.js', 'pnpm']/['pnpm workspace']/[], F1=0.9474; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=False
- ANN-0003: title raw=True, normalized=True; skills TP/FP/FN=['Apache Kafka', 'Ceph', 'Docker', 'Helm', 'Kubernetes', 'Linux', 'Milvus', 'MongoDB', 'MySQL', 'Redis']/['Helm Chart', '分布式系统']/['可观测性'], F1=0.8696; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=False
- ANN-0004: title raw=True, normalized=True; skills TP/FP/FN=['AutoGPT', 'LangChain', 'LangGraph', '向量数据库', '多智能体协同', '大模型API', '工具调用', '微服务', '提示工程', '消息队列', '记忆机制']/['Python', '大模型微调', '提示词']/[], F1=0.8800; bonus TP/FP/FN=['大模型微调', '提示词']/[]/['Python'], F1=0.8000; education=True
- ANN-0005: title raw=True, normalized=True; skills TP/FP/FN=['Apache Spark', 'C', 'C#', 'C++', 'DB2', 'HBase', 'Hadoop', 'Hive', 'Java', 'Linux', 'MapReduce', 'MySQL', 'Oracle', 'Python', 'SQL', 'Storm', 'Unix', '存储过程']/['ETL']/['SQL Server'], F1=0.9474; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0006: title raw=True, normalized=True; skills TP/FP/FN=['Python', '数据分析', '数据处理', '统计分析', '自动驾驶技术栈']/['数据挖掘']/[], F1=0.9091; bonus TP/FP/FN=['数据标注', '质量保障']/['自动驾驶技术栈', '评测']/['数据评测', '自动驾驶数据制备'], F1=0.5000; education=True
- ANN-0007: title raw=True, normalized=True; skills TP/FP/FN=['CI/CD', 'Java', 'React', 'Spring Boot', 'Spring Cloud', 'Vue.js', '自动化测试']/[]/['AI辅助编程'], F1=0.9333; bonus TP/FP/FN=['IT基础设施', 'LIMS', 'MES', 'QMS', '任务调度']/[]/[], F1=1.0000; education=True
- ANN-0008: title raw=True, normalized=True; skills TP/FP/FN=['CSS3', 'HTML5', 'JavaScript', 'React', 'React Native', 'Taro', 'TypeScript', 'Vue.js', '前端工程化', '模块化']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=False
- ANN-0009: title raw=False, normalized=True; skills TP/FP/FN=['Airflow', 'Apache Flink', 'Apache Kafka', 'Apache Spark', 'DolphinScheduler', 'ETL', 'HBase', 'HDFS', 'Hadoop', 'Hive SQL', 'Java', 'Python', 'SQL', 'Scala', 'XXL-Job', 'YARN', 'ZooKeeper', '数据仓库建模']/['ClickHouse', 'Doris', 'Hive', '数据仓库', '数据治理']/[], F1=0.8780; bonus TP/FP/FN=['ClickHouse', 'Doris']/[]/['数据治理', '高并发数据处理'], F1=0.6667; education=True
- ANN-0010: title raw=True, normalized=True; skills TP/FP/FN=['Cocos Creator', 'ECharts', 'React', 'Taro', 'TypeScript', 'Vite', 'Vue.js', 'Webpack', '前端工程化']/['数据可视化']/[], F1=0.9474; bonus TP/FP/FN=['AI对话', '数字人', '智慧大屏']/[]/[], F1=1.0000; education=False
- ANN-0011: title raw=True, normalized=True; skills TP/FP/FN=['C', 'C++', 'DDD', 'IOC', 'Linux', 'Windows', '内存', '图形性能', '设计模式', '跨平台UI', '面向对象']/['组件式']/['并行性能'], F1=0.9167; bonus TP/FP/FN=['组件式']/[]/['视觉软件PC端'], F1=0.6667; education=True
- ANN-0012: title raw=False, normalized=True; skills TP/FP/FN=['C', 'C++', 'Django', 'FFmpeg', 'FastAPI', 'Flask', 'GB28181', 'H.264', 'H.265', 'HTTP/HTTPS', 'MySQL', 'ONVIF', 'PostgreSQL', 'Python', 'RESTful API', 'RTMP', 'RTSP', 'Redis', 'SIP', 'TCP/IP']/['HLS', 'HTTP-FLV', 'WebRTC']/[], F1=0.9302; bonus TP/FP/FN=[]/['FFmpeg', 'GB28181', 'H.264', 'H.265', 'ONVIF', 'RTMP', 'RTSP', 'SIP']/['Python ONVIF', '安防监控', '流媒体对接', '音视频'], F1=0.0000; education=True
- ANN-0013: title raw=True, normalized=True; skills TP/FP/FN=['Apache Spark', 'D3.js', 'ECharts', 'Hadoop', 'Matplotlib', 'Power BI', 'Python', 'Seaborn', 'Tableau', '数据可视化', '概率论', '统计学']/['数据分析']/[], F1=0.9600; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0014: title raw=True, normalized=True; skills TP/FP/FN=['DID', 'Python', 'R', 'SQL', '因果推断', '大数据处理', '工具变量', '数据可视化', '断点回归', '机器学习', '统计学']/['数据清洗', '特征构建']/[], F1=0.9167; bonus TP/FP/FN=[]/['因果推断']/['政策效应评估'], F1=0.0000; education=True
- ANN-0015: title raw=True, normalized=True; skills TP/FP/FN=['AWS', 'Ansible', 'Azure', 'CI/CD', 'Confluence', 'Datadog', 'Docker', 'Git', 'Grafana', 'ITIL', 'JBoss', 'JIRA', 'Jenkins', 'Linux', 'Maven', 'MongoDB', 'Oracle', 'PostgreSQL', 'Prometheus', 'Puppet', 'Python', 'Ruby', 'Shell', 'Terraform', 'Tomcat', '敏捷方法', '阿里云']/['AWS EC2', 'CloudFormation', 'Route53', 'VPC']/[], F1=0.9310; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0016: title raw=False, normalized=True; skills TP/FP/FN=['Chroma', 'Claude Code', 'Django', 'FastAPI', 'Flask', 'GitHub Copilot', 'Milvus', 'Pinecone', 'Python', '全栈', '向量数据库', '提示工程', '文档解析', '检索增强生成', '知识库工程化']/['React', 'Vue.js', '切片', '召回']/[], F1=0.8824; bonus TP/FP/FN=['React', 'Vue.js']/[]/[], F1=1.0000; education=False
- ANN-0017: title raw=False, normalized=True; skills TP/FP/FN=['ArgoCD', 'CI/CD', 'Ceph', 'DevOps', 'GitLab', 'GlusterFS', 'Go', 'Harbor', 'Java', 'Jenkins', 'JuiceFS', 'Kubernetes', 'Linux', 'Python', 'SonarQube', 'TCP/IP', '云原生', '分布式存储', '监控告警', '自动化测试']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0018: `fallback` — LLMExtractionError: provider call failed
- ANN-0019: title raw=True, normalized=True; skills TP/FP/FN=['LSTM', 'Python', 'SVR', '大语言模型', '数据治理', '文本解析', '时序预测', '机器学习', '语义分析', '随机森林']/['数据分析']/['B端平台全生命周期'], F1=0.9091; bonus TP/FP/FN=[]/[]/['备件智能', '风电备件智能化'], F1=0.0000; education=True
- ANN-0020: title raw=True, normalized=True; skills TP/FP/FN=['Docker', 'Git', 'Pandas', 'Python', 'SQL']/['自然语言处理']/[], F1=0.9091; bonus TP/FP/FN=['自然语言处理']/[]/[], F1=1.0000; education=True
- ANN-0021: title raw=True, normalized=True; skills TP/FP/FN=['DLP', 'EDR', 'IAM', 'PAM', '云平台安全', '安全产品', '最小权限原则']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0022: title raw=True, normalized=True; skills TP/FP/FN=['Java', 'MyBatis', 'MySQL', 'RESTful API', 'SQL', 'Spring Boot']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0023: title raw=True, normalized=True; skills TP/FP/FN=['Altium Designer', 'C', 'CPLD', 'FPGA', 'Quartus', 'STM32', 'Verilog', '原理图', '数字电路', '模拟电路', '汇编', '硬件设计']/['Protel99se']/['51单片机', 'PCB设计', 'Protel 99SE'], F1=0.8571; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0024: title raw=True, normalized=True; skills TP/FP/FN=['Apache Kafka', 'CAP', 'CI/CD', 'DDD', 'Docker', 'Dubbo', 'Elasticsearch', 'IO', 'JVM', 'Java', 'Kubernetes', 'MySQL', 'PostgreSQL', 'Redis', 'RocketMQ', 'Spring Cloud', 'TOGAF', '一致性', '分布式', '分布式系统', '大语言模型', '并发编程', '微服务', '数据结构', '机器学习', '网络通信']/['SQL', '分库分表']/[], F1=0.9630; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=False
- ANN-0025: title raw=False, normalized=True; skills TP/FP/FN=['CSS3', 'ECharts', 'ElementUI', 'HTML5', 'JavaScript', 'Three.js', 'Vite', 'Vue.js', 'Webpack', '前端工程化', '性能调优', '跨浏览器适配']/['数据可视化', '组件化']/[], F1=0.9231; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0026: title raw=True, normalized=True; skills TP/FP/FN=['Charles', 'Fiddler', 'JMeter', 'Linux', 'Postman', '功能测试', '接口测试', '测试方法', '测试理论']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0027: title raw=False, normalized=True; skills TP/FP/FN=['BM25', 'Elasticsearch', 'Python', '信息检索', '分词策略', '向量数据库', '向量检索', '提示工程', '文档切片', '检索增强生成', '混合检索', '重排序']/['AB实验', '分词', '切片策略', '意图识别', '时间语义解析', '核心词抽取']/[], F1=0.8000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0028: title raw=False, normalized=True; skills TP/FP/FN=['Adapter', 'BERT', 'C++', 'CI/CD', 'GPT', 'LoRA', 'MindSpore', 'MoE', 'ONNX', 'PyTorch', 'Python', 'TensorFlow', 'TensorRT', 'Transformer', 'vLLM', '推理部署', '检索增强生成', '模型微调', '模型蒸馏', '量化压缩']/['Docker', 'Mindspore', '多模态模型', '大语言模型']/[], F1=0.9091; bonus TP/FP/FN=['Ascend']/[]/[], F1=1.0000; education=True
- ANN-0029: title raw=True, normalized=True; skills TP/FP/FN=['Echo', 'Gin', 'Go', 'HTTP', 'Linux', 'MySQL', 'Redis', 'Shell', 'gRPC', '并发处理', '微服务']/[]/[], F1=1.0000; bonus TP/FP/FN=['代理服务', '任务调度']/[]/[], F1=1.0000; education=True
- ANN-0030: title raw=False, normalized=False; skills TP/FP/FN=['CSS', 'ElementUI', 'HTML', 'Java', 'JavaScript', 'MyBatis', 'MySQL', 'Oracle', 'React', 'SQL', 'Spring Boot', 'Spring Cloud', 'Vant', 'Vue.js']/['ECharts']/['数据可视化'], F1=0.9333; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0031: title raw=True, normalized=True; skills TP/FP/FN=['C', 'C++', 'Linux', 'MySQL', 'STL', 'Windows', '多线程', '多进程', '数据结构', '网络编程']/['UE']/['Visual Studio'], F1=0.9091; bonus TP/FP/FN=['UE']/[]/['仿真系统'], F1=0.6667; education=True
- ANN-0032: title raw=True, normalized=True; skills TP/FP/FN=['C++', 'MATLAB', 'Python', '数据分析', '数据库', '算法部署']/[]/['数据建模', '数据挖掘', '数据清洗'], F1=0.8000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0033: title raw=True, normalized=True; skills TP/FP/FN=['Dubbo', 'JPA', 'Java', 'MyBatis', 'MySQL', 'OpenGauss', 'Oracle', 'SQL', 'Spring Cloud Alibaba', '分布式', '多线程', '并发编程', '消息队列', '缓存', '网络编程', '设计模式', '负载均衡', '达梦数据库', '高并发', '高负载']/['openGauss', '数据模型', '消息', '面向对象分析']/['高可用'], F1=0.8889; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0034: title raw=True, normalized=True; skills TP/FP/FN=['信号分析仪', '信号降噪', '声学数据处理', '声学测试分析', '水听器', '水声信号处理', '水声换能器', '示波器', '频谱分析']/['声学材料', '水声传感器件']/[], F1=0.9000; bonus TP/FP/FN=[]/[]/['传感器件研发', '声学材料工艺', '水下声学测试'], F1=0.0000; education=True
- ANN-0035: title raw=True, normalized=True; skills TP/FP/FN=['', 'Apache Flink', 'Apache Kafka', 'Apache Spark', 'Dubbo', 'Elasticsearch', 'Go', 'Hive', 'Java', 'Python', 'RabbitMQ', 'Redis', 'Spring Cloud', '信用评分', '反欺诈', '微服务', '数据结构', '风控策略']/['模型', '特征工程', '规则']/[], F1=0.9231; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0036: title raw=False, normalized=True; skills TP/FP/FN=['WBS', '门径', '非标自动化产品']/['质量']/['机电软多专业协同', '特种机器人产品', '项目管理'], F1=0.6000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0037: title raw=False, normalized=True; skills TP/FP/FN=['React', 'Vite', 'Vue.js', 'Webpack', '兼容性处理', '前端', '前端工程化', '前端性能']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0038: title raw=True, normalized=True; skills TP/FP/FN=['C++', 'Python', '数据分析', '系统设计']/[]/['数据挖掘'], F1=0.8889; bonus TP/FP/FN=['数据闭环', '模型训练']/[]/[], F1=1.0000; education=False
- ANN-0039: title raw=True, normalized=True; skills TP/FP/FN=['Linux', 'MySQL', 'Python', 'SQL', '教学能力', '课程']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0040: title raw=False, normalized=True; skills TP/FP/FN=['功能测试', '性能测试', '测试用例', '测试管理']/[]/['银行零售业务'], F1=0.8889; bonus TP/FP/FN=[]/['零售贷款测试']/['零售贷款类测试'], F1=0.0000; education=True
- ANN-0041: title raw=False, normalized=True; skills TP/FP/FN=['Ant Design', 'CSS3', 'D3.js', 'ECharts', 'ElementUI', 'Git', 'HTML5', 'HTTP', 'JavaScript', 'Less', 'React', 'SVN', 'Sass', 'TypeScript', 'Vite', 'Vue.js', 'WebSocket', 'Webpack']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0042: title raw=True, normalized=True; skills TP/FP/FN=['交易流程', '功能测试', '接口自动化测试', '测试管理', '自动化测试', '账务逻辑', '软件测试']/['JMeter', 'Postman']/['结售汇业务', '衍生品交易'], F1=0.7778; bonus TP/FP/FN=['结售汇测试']/[]/[], F1=1.0000; education=False
- ANN-0043: title raw=True, normalized=True; skills TP/FP/FN=['K-Means', 'NumPy', 'Pandas', 'Python', 'SQL', 'scikit-learn', '决策树', '大数据', '数据仓库', '数据分析', '数据清洗', '数据集市', '机器学习', '模型评估', '特征工程', '线性回归', '逻辑回归', '随机森林']/['Apache Spark', 'Hive', 'Matplotlib', 'PowerBI', 'Seaborn', 'Tableau', '统计学']/['AI项目流程', '数据可视化'], F1=0.8000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0044: title raw=True, normalized=True; skills TP/FP/FN=['Doris', 'ETL调度监控', 'Excel', 'MySQL', 'PostgreSQL', 'Power BI', 'SQL', '帆软', '报表系统', '数据分析', '数据可视化', '数据运营']/['ETL', 'Hive', 'Linux', 'Python']/['SQL性能'], F1=0.8276; bonus TP/FP/FN=['Hive', 'Linux', 'Python']/[]/[], F1=1.0000; education=True
- ANN-0045: title raw=True, normalized=True; skills TP/FP/FN=['ARM', 'C++', 'HTTP', 'IPC', 'JSON', 'Linux', 'Protobuf', 'Rust', 'SQLite', 'Shell', 'Socket', 'TCP', 'UDP', '内存', '多线程', '多进程', '并发安全']/['IEC104', 'Modbus', '嵌入式开发', '异步编程', '网络编程', '进程间通信', '高并发']/['并发编程', '异步IO'], F1=0.7907; bonus TP/FP/FN=['IEC104', 'Modbus']/[]/[], F1=1.0000; education=True
- ANN-0046: title raw=True, normalized=True; skills TP/FP/FN=['BI开发', 'CDH', 'CSS', 'Greenplum', 'HTML', 'JavaScript', 'SQL', 'SQL调优', 'TDH', '存储过程', '数据可视化']/['BI', 'FineBi', 'FineReport']/[], F1=0.8800; bonus TP/FP/FN=['国产大数据']/['华为', '星环', '腾讯', '证券行业业务逻辑', '证券项目实施', '阿里', '顶点交易柜台']/[], F1=0.2222; education=True
- ANN-0047: title raw=True, normalized=True; skills TP/FP/FN=['Apifox', 'Linux', 'MySQL', 'Oracle', 'Postman', 'SQL', 'Shell', '接口测试', '测试理论']/['JMeter', '自动化测试', '软件测试']/['测试方法', '测试用例设计'], F1=0.7826; bonus TP/FP/FN=['UI自动化']/['恒生柜台', '证券业务流程']/['接口自动化'], F1=0.4000; education=True
- ANN-0048: title raw=False, normalized=True; skills TP/FP/FN=['BPM', 'CI/CD', 'DDD', 'Docker', 'Go', 'Java', 'Kubernetes', 'Rancher', 'Spring Boot', 'Spring Cloud', 'Spring Cloud Alibaba', '分布式系统', '可观测性', '微服务', '高并发']/['架构设计', '链路追踪', '领域驱动设计']/['API网关'], F1=0.8824; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=False
- ANN-0049: title raw=True, normalized=True; skills TP/FP/FN=['数学建模', '数据分析', '数据挖掘', '模型求解', '算法', '运筹']/['Python']/[], F1=0.9231; bonus TP/FP/FN=['Python']/['数学建模竞赛']/[], F1=0.6667; education=True
- ANN-0050: title raw=True, normalized=True; skills TP/FP/FN=['ARM', 'C', 'DSP', 'FPGA', 'STM32', '嵌入式', '控制系统']/['控制', '驱动']/[], F1=0.8750; bonus TP/FP/FN=[]/['航天航空']/[], F1=0.0000; education=False
- ANN-0051: `fallback` — LLMExtractionError: provider call failed
- ANN-0052: title raw=False, normalized=False; skills TP/FP/FN=['Docker', 'FastAPI', 'Flask', 'FunctionCalling', 'JWT', 'Memory', 'MySQL', 'PyMySQL', 'Python', 'RESTful API', 'React', 'SQLAlchemy', 'SSE', 'ToolCalling', '流式输出']/['Dockerfile', 'docker-compose']/['Agentic AI', 'LLM API', 'OpenAI SDK'], F1=0.8571; bonus TP/FP/FN=['MinIO', 'S3', '提示工程', '检索增强生成']/['Agentic AI', 'Memory', 'React', 'SSE', '多轮对话编排']/['多轮对话'], F1=0.5714; education=True
- ANN-0053: title raw=False, normalized=True; skills TP/FP/FN=['BurpSuite', 'Cknife', 'IPS', 'Kali', 'Linux', 'Nmap', 'PKI/CA', 'Python', 'Shell', 'Sqlmap', 'VPN', 'WinHex', 'Wireshark', 'Xsser', '入侵检测', '安全', '应用安全', '渗透测试', '漏洞扫描', '系统加固', '防火墙']/['H3C', '华为', '思科', '深信服', '网络安全', '网络攻击']/[], F1=0.8750; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0054: title raw=False, normalized=True; skills TP/FP/FN=['APS', 'C++', 'CV', 'PyTorch', 'Python', 'TensorFlow', '句法分析', '大语言模型', '情感分析', '机器学习', '模型训练', '深度学习', '特征提取', '自然语言处理', '词法分析', '语义理解']/['优化', '计算机视觉', '运筹学']/[], F1=0.9143; bonus TP/FP/FN=['优化', '运筹学']/[]/[], F1=1.0000; education=True
- ANN-0055: title raw=False, normalized=True; skills TP/FP/FN=['C', 'C++', 'ETL', 'Java', 'Linux', 'Python', '协同过滤', '场景', '大数据', '推荐', '推荐算法', '用户画像']/['用户画像建模']/[], F1=0.9600; bonus TP/FP/FN=['用户画像建模']/[]/['DMP'], F1=0.6667; education=True
- ANN-0056: title raw=False, normalized=True; skills TP/FP/FN=['AI模型聚合', 'API网关', 'Docker', 'Gin', 'Go', 'Kubernetes', 'MySQL', 'NewAPI', 'OpenAI', 'Redis', '分布式系统', '流式传输', '消息队列', '熔断降级', '负载均衡', '限流', '高并发']/['OpenAI兼容', '云原生', '大模型API', '限流风控']/['大模型API对接'], F1=0.8718; bonus TP/FP/FN=['商业化API', '支付计费']/['API网关', 'NewAPI']/[], F1=0.6667; education=True
- ANN-0057: title raw=True, normalized=True; skills TP/FP/FN=['', '召回', '推荐', '数据结构', '机器学习', '深度学习', '混排', '粗排', '精排']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/['推荐']/['大规模推荐', '搜索引擎', '计算广告'], F1=0.0000; education=False
- ANN-0058: title raw=False, normalized=False; skills TP/FP/FN=['优化', '数据挖掘', '机器学习']/['DL', 'ML', 'RL', '数理统计', '机制', '运筹']/['强化学习', '数学建模', '概率统计', '深度学习'], F1=0.3750; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0059: title raw=True, normalized=True; skills TP/FP/FN=['信息安全', '公文写作', '安全培训', '安全检查', '安全风险排查', '应急', '数据安全', '等级保护']/[]/['安全审计'], F1=0.9412; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=False
- ANN-0060: title raw=True, normalized=True; skills TP/FP/FN=['CSS', 'Git', 'HTML', 'Java', 'JavaScript', 'MySQL', 'Oracle', 'PostgreSQL', 'RESTful API', 'React', 'Spring Boot', 'TypeScript', '性能调优', '接口设计', '数据库']/['Agentic AI', 'LLM API', '检索增强生成']/['AI能力集成', '后端工程基础'], F1=0.8571; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=False
- ANN-0061: title raw=False, normalized=True; skills TP/FP/FN=['BSP', 'C', 'C++', '代码审查', '传感器数据融合', '嵌入式编程', '模型导出', '模型轻量化', '深度学习', '硬件调试', '系统调试', '通讯协议', '驱动']/['PyTorch', 'TensorFlow', '传感器融合', '系统调优']/['AI模型部署', '模型剪枝', '模型蒸馏', '模型转换', '模型量化', '系统架构', '边缘部署'], F1=0.7027; bonus TP/FP/FN=['交换机', '硬件', '视频']/[]/[], F1=1.0000; education=True
- ANN-0062: title raw=False, normalized=True; skills TP/FP/FN=['CI/CD', 'DevOps', 'DevOps工具链', 'Django', 'Flask', 'Go', 'Linux', 'Python', 'Shell', 'TCP/IP', '公有云', '安全策略', '性能分析', '数据备份', '日志分析', '漏洞修复', '监控告警', '系统巡检', '自动化']/['Ansible', 'Git', 'Jenkins', '腾讯云', '阿里云']/['Docker', '容灾恢复'], F1=0.8444; bonus TP/FP/FN=[]/[]/['CMDB', '持续集成平台', '自动化运维平台'], F1=0.0000; education=False
- ANN-0063: title raw=False, normalized=False; skills TP/FP/FN=['OpenCV', 'PyTorch', 'Python', 'TensorFlow', '数字信号处理', '数据预处理', '机器学习', '模型', '深度学习', '特征工程', '算法工程化', '视频融合', '计算机视觉']/['TensorRT', '视频分析']/['模型推理'], F1=0.8966; bonus TP/FP/FN=['TensorRT']/['视频分析']/[], F1=0.6667; education=True
- ANN-0064: title raw=False, normalized=True; skills TP/FP/FN=['ARM Cortex-M', 'C', 'C++', 'CAN', 'Ethernet', 'FreeRTOS', 'I2C', 'MQTT', 'OTA', 'RISC-V', 'RTOS', 'SPI', 'TCP/IP', 'UART', 'USB', '传感器融合', '原理图', '固件', '嵌入式开发', '插补', '数字电路', '模拟电路', '步进电机控制', '硬件调试', '路径规划', '运动学模型', '运动控制', '驱动']/['EMC', 'EMI', 'FDM', 'Klipper', 'Linux', 'Marlin', 'SLA', '信号完整性', '电源']/['G代码解析', 'Linux内核', 'PID控制'], F1=0.8235; bonus TP/FP/FN=[]/[]/['3D打印控制系统'], F1=0.0000; education=True
- ANN-0065: title raw=True, normalized=True; skills TP/FP/FN=['ElementUI', 'Python', 'Vue.js', '全栈', '前端', '后端', '微服务', '性能调优', '故障处理', '数据库']/['数据可视化']/['RESTful API'], F1=0.9091; bonus TP/FP/FN=['数据可视化']/[]/['AI应用'], F1=0.6667; education=True
- ANN-0066: title raw=True, normalized=True; skills TP/FP/FN=['AI工作流', 'AI应用', 'AI模型调用', 'Elasticsearch', 'Go', 'MCP', 'Python', '接口', '数据安全合规', '数据治理', '数据质量管控', '数据资产', '数据采集', '系统架构', '跨系统集成']/['Skill', '数据资产梳理', '需求分析']/['AI模型微调', 'AI模型部署', '数据整合', '数据标准化', '数据清洗'], F1=0.7895; bonus TP/FP/FN=[]/['公安系统', '政府数据治理']/[], F1=0.0000; education=False
- ANN-0067: title raw=False, normalized=True; skills TP/FP/FN=['AI辅助编程', 'CI/CD', 'CNCF', 'DevOps', 'Docker', 'HTTPS', 'IaC', 'Kubernetes', 'OAuth2', '云原生', '云安全', '凭证', '多云', '多租户', '容器化', '微服务', '证书', '高可用']/['基础设施即代码']/['Kubernetes存储', 'Kubernetes网络', '公有云'], F1=0.9000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=False
- ANN-0068: title raw=False, normalized=True; skills TP/FP/FN=['ARM', 'BSP', 'C', 'C++', 'DRM', 'DTS', 'H.264', 'H.265', 'HDMI', 'HTTP', 'I2C', 'I2S', 'ISP', 'Linux驱动', 'MIPI-CSI', 'MJPEG', 'Makefile', 'Python', 'RTMP', 'RTSP', 'SPI', 'Shell', 'UART', 'USB', 'V4L2', '平台设备驱动', '芯片功能验证', '设备树', '音视频编解码']/['Audio', 'BT', 'Bring-up', 'Camera', 'LCD', 'Linux', 'WiFi', '设备驱动']/['Camera驱动', '字符设备驱动'], F1=0.8529; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=False
- ANN-0069: title raw=False, normalized=False; skills TP/FP/FN=['', 'Go', 'Linux', 'MySQL', 'Redis', '分布式系统', '可扩展', '数据结构', '服务化', '消息队列', '系统架构', '高可用']/['大语言模型']/['代码审查', '监控预警'], F1=0.8889; bonus TP/FP/FN=[]/['大语言模型']/['大模型应用'], F1=0.0000; education=True
- ANN-0070: title raw=False, normalized=False; skills TP/FP/FN=['', 'CSS', 'HTML', 'Java', 'JavaScript', 'Spring Boot', 'Web开发', '数据结构', '系统设计', '网络编程']/[]/['性能调优'], F1=0.9524; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=False
- ANN-0071: title raw=False, normalized=True; skills TP/FP/FN=['', 'Benchmark', 'CUDA', 'CV', 'Docker', 'Linux', 'LoRA', 'PyTorch', 'Python', 'QLoRA', 'TensorFlow', 'Transformer', '大语言模型', '推理加速', '数据结构', '模型', '模型量化', '自然语言处理']/[]/['AI评测体系', '多模态AI', '数据构建', '模型微调'], F1=0.9000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0072: title raw=True, normalized=True; skills TP/FP/FN=['Angular', 'CI/CD', 'Django', 'Docker', 'Express.js', 'Java', 'JavaScript', 'Kubernetes', 'Linux', 'MongoDB', 'MySQL', 'Nginx', 'Node.js', 'PostgreSQL', 'Python', 'RESTful API', 'React', 'Redis', 'Spring Boot', 'Vue.js', 'Webpack', '实时通信', '数据安全', '高并发']/['ES6+']/[], F1=0.9796; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0073: title raw=False, normalized=True; skills TP/FP/FN=['C', 'C++', 'EMC', 'PCB', '以太网', '单片机', '单片机操作', '原理图', '器件选型', '嵌入式开发', '应用层', '底层驱动', '硬件设计', '驱动']/[]/['硬件调试'], F1=0.9655; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0074: title raw=False, normalized=False; skills TP/FP/FN=['Web安全', '公有云安全', '合规审计', '密保测评', '漏洞扫描', '病毒木马防范', '程序漏洞检测', '终端安全', '角色权限', '访问控制', '身份认证', '防DDOS']/['WEB安全', '网络安全法律法规']/['安全治理', '安全管理体系', '等保测评', '网络安全规划'], F1=0.8000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=False
- ANN-0075: title raw=False, normalized=True; skills TP/FP/FN=['Apache Flink', 'Apache Spark', 'Doris', 'ETL', 'HBase', 'Hadoop', 'JVM', 'Java', 'MyBatis', 'Ranger', 'SQL', 'Spring Boot', '分布式系统', '多线程', '数据仓库', '数据采集', '服务化', '维度建模', '网络编程']/['BI', '元数据']/['JVM调优', 'SQL性能', '数据安全', '数据治理'], F1=0.8636; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0076: title raw=True, normalized=True; skills TP/FP/FN=['ARM', 'C', 'C++', 'CAN', 'I2C', 'Linux', 'MCU', 'RS-232', 'RS-485', 'RTOS', 'SPI', 'TCP/IP', 'UART', 'UDP', '传感器', '嵌入式开发', '电机控制', '驱动']/['STM32', '裸机']/['硬件调试', '软硬件联调'], F1=0.9000; bonus TP/FP/FN=[]/['无人机', '智能硬件', '机器人', '自动化设备']/[], F1=0.0000; education=True
- ANN-0077: title raw=False, normalized=True; skills TP/FP/FN=['AIGC', 'RL', '世界模型', '偏好对齐', '多模态CoT', '多模态理解', '强化学习', '指令微调', '数据合成', '数据建设', '智能Agent', '机器学习', '模型推理', '物理保真生成', '物理渲染', '自然语言处理', '视频生成', '计算机视觉', '评测体系']/['多模态模型', '标注体系构建']/['图像生成', '多模态生成'], F1=0.9048; bonus TP/FP/FN=['C++', 'Python']/['CV', 'ML', 'RL', '机器学习', '自然语言处理']/['C'], F1=0.4000; education=True
- ANN-0078: title raw=True, normalized=True; skills TP/FP/FN=['CSS', 'HTML', 'JavaScript', 'React', 'Vue.js', '兼容性测试', '前端', '性能调优', '接口联调', '组件库']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/['React']/[], F1=0.0000; education=True
- ANN-0079: title raw=False, normalized=True; skills TP/FP/FN=['Agentic AI', 'Apache Flink', 'Apache Kafka', 'Apache Spark', 'CV', 'ETL', 'FastAPI', 'Hadoop', 'Hive', 'Java', 'Linux', 'MLOps', 'PyTorch', 'Python', 'SQL', 'TensorFlow', '多模态交互', '大语言模型', '数据仓库', '机器学习', '检索增强生成', '深度学习', '自然语言处理']/['计算机视觉']/['数据存储', '数据建模', '数据清洗', '数据计算', '数据采集'], F1=0.8846; bonus TP/FP/FN=['LoRA', 'QLoRA', '向量数据库']/['Agentic AI', '检索增强生成']/['模型推理部署'], F1=0.6667; education=True
- ANN-0080: title raw=True, normalized=True; skills TP/FP/FN=['Apache', 'CSS3', 'Django', 'Express.js', 'Flask', 'HTML5', 'JavaScript', 'Laravel', 'Linux', 'MySQL', 'NestJS', 'Nginx', 'Node.js', 'PHP', 'PostgreSQL', 'Python', 'React', 'Vue.js']/[]/['域名配置', '性能调优'], F1=0.9474; bonus TP/FP/FN=['Docker']/[]/[], F1=1.0000; education=False
- ANN-0081: title raw=True, normalized=True; skills TP/FP/FN=['Python', '大语言模型', '强化学习', '数据并行', '机器学习', '模型并行', '深度学习', '迁移学习']/['PyTorch', 'TensorFlow', '大模型算法']/['模型微调'], F1=0.8000; bonus TP/FP/FN=[]/['GPT', 'LLaMA']/[], F1=0.0000; education=True
- ANN-0082: title raw=False, normalized=True; skills TP/FP/FN=['C', 'C++', 'Linux', 'RTOS', 'SMP', '中断', '任务调度', '内存', '多核编程', '存储', '嵌入式', '线程', '设计模式', '面向对象']/['功耗']/[], F1=0.9655; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0083: title raw=False, normalized=False; skills TP/FP/FN=['', 'C', 'C++', 'Go', 'Java', 'Python', '数据结构', '设计模式']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=False
- ANN-0084: title raw=True, normalized=True; skills TP/FP/FN=['C', 'C++', '传感器', '信号处理', '单片机', '嵌入式开发', '底层', '硬件驱动']/['抗干扰']/[], F1=0.9412; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0085: title raw=True, normalized=True; skills TP/FP/FN=['Ansible', 'BGP', 'CDN', 'CI/CD', 'DNS', 'Docker', 'ELK', 'Grafana', 'HTTP', 'Kubernetes', 'Linux', 'Prometheus', 'Python', 'Shell', 'TCP/IP', 'TLS', 'Terraform']/['AWS', 'Aliyun', 'CloudWatch', 'EKS', 'GitHub Actions', 'GitLab CI', 'IAM', 'Jenkins', 'KMS', 'OCI Monitoring', 'Oracle Cloud', 'Secrets Manager', 'VCN', 'VPC', 'Vault', 'WAF', '负载均衡']/['公有云'], F1=0.6538; bonus TP/FP/FN=['Service Mesh']/[]/[], F1=1.0000; education=True
- ANN-0086: title raw=True, normalized=True; skills TP/FP/FN=['AI辅助编程', 'CSS3', 'Element UI', 'HTML5', 'Java', 'JavaScript', 'MySQL', 'Redis', 'SQL', 'Spring Boot', 'TypeScript', 'Vue.js', 'uni-app']/['Flex', 'Grid', 'Uniapp UI', '响应式']/[], F1=0.8667; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0087: title raw=False, normalized=True; skills TP/FP/FN=['Java', 'Linux', '自动化运维']/['Ansible', 'Docker', 'Kubernetes', 'Shell']/['运维脚本'], F1=0.5455; bonus TP/FP/FN=['Docker', 'Kubernetes']/[]/[], F1=1.0000; education=True
- ANN-0088: title raw=True, normalized=True; skills TP/FP/FN=['Angular', 'Apache Spark', 'CSS3', 'Django', 'Flask', 'Go', 'HTML5', 'Hadoop', 'Java', 'JavaScript', 'MongoDB', 'MySQL', 'Node.js', 'PostgreSQL', 'PyTorch', 'Python', 'React', 'Redis', 'Spring Boot', 'TensorFlow', 'TypeScript', 'Vue.js']/['AWS', 'Docker', 'Kubernetes', '云原生', '多模态数据处理', '大模型训练', '数据仓库', '腾讯云', '阿里云']/['公有云'], F1=0.8148; bonus TP/FP/FN=['Docker', 'Kubernetes']/['多模态数据处理']/[], F1=0.8000; education=True
- ANN-0089: title raw=False, normalized=True; skills TP/FP/FN=['ARM', 'BACnet', 'C', 'C++', 'CAN', 'FreeRTOS', 'HTTP', 'LoRaWAN', 'MQTT', 'Modbus', 'OPC UA', 'RS485', 'RT-Thread', 'STM32', 'TCP/IP', '嵌入式开发']/['BACnet IP', 'MCU']/[], F1=0.9412; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0090: title raw=True, normalized=True; skills TP/FP/FN=['CoAP', 'Docker', 'HTTP', 'Java', 'Kubernetes', 'MQTT', 'MongoDB', 'MySQL', 'Redis', 'Spring Boot', 'Spring Cloud', 'TCP', 'UDP', '微服务', '高并发']/['分布式', '服务熔断', '限流', '高可用']/[], F1=0.8824; bonus TP/FP/FN=[]/['工业物联网', '能耗']/[], F1=0.0000; education=True
- ANN-0091: title raw=True, normalized=True; skills TP/FP/FN=['AI辅助编程', 'Ant Design Vue', 'Axios', 'CSS3', 'ElementUI', 'Git', 'HTML5', 'Java', 'JavaScript', 'MyBatis', 'MySQL', 'Oracle', 'Pinia', 'RESTful API', 'Spring Boot', 'Spring MVC', 'Vite', 'Vue.js', 'Vuex']/['AI提示词工程', 'Claude Code', 'Cursor', 'ES6+', 'Flex', 'GitHub Copilot', 'Grid', 'SQL', 'Vibe Coding', 'Vue-CLI', 'VueRouter']/['Element UI', 'Vue Router', '提示词工程'], F1=0.7308; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=False
- ANN-0092: title raw=False, normalized=True; skills TP/FP/FN=['Docker', 'Dubbo', 'IO', 'Java', 'MyBatis', 'NIO', 'RocketMQ', 'Spring Boot', '多线程', '设计模式', '通信']/['分布式服务']/['分布式系统'], F1=0.9167; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0093: title raw=False, normalized=True; skills TP/FP/FN=['Apache Kafka', 'Java', 'MySQL', 'RabbitMQ', 'Redis', 'Spring Boot', 'Spring Cloud', '分布式系统', '接口安全', '风控']/['接口设计', '系统分层', '高并发']/[], F1=0.8696; bonus TP/FP/FN=[]/['接口安全', '海外', '跨境业务', '风控']/[], F1=0.0000; education=False
- ANN-0094: title raw=True, normalized=True; skills TP/FP/FN=['Java', 'MyBatis', 'MySQL', 'RabbitMQ', 'Redis', 'Spring Boot', '微服务']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0095: title raw=True, normalized=True; skills TP/FP/FN=['API', 'CSS3', 'Go', 'HTML5', 'Java', 'JavaScript', 'Node.js', 'React', 'TypeScript', 'Vue.js', '微服务', '数据库']/['前后端分离']/[], F1=0.9600; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=False
- ANN-0096: title raw=True, normalized=True; skills TP/FP/FN=['Java', 'MyBatis', 'MySQL', 'Spring Boot']/['SQL', '微服务']/[], F1=0.8000; bonus TP/FP/FN=['微服务']/[]/['消息队列'], F1=0.6667; education=True
- ANN-0097: title raw=False, normalized=True; skills TP/FP/FN=['Apache Kafka', 'Go', 'Go-zero', 'Java', 'Linux', 'MySQL', 'PHP', 'RTC', 'Redis', 'RocketMQ', 'Spring Boot', 'WebSocket']/['IM即时通讯', '分布式']/[], F1=0.9231; bonus TP/FP/FN=[]/[]/['IM/直播SDK', '分布式系统', '第三方支付对接'], F1=0.0000; education=True
- ANN-0098: title raw=True, normalized=True; skills TP/FP/FN=['', 'NoSQL数据库', 'Python', 'Web', '数据结构']/['Django', 'Flask', 'MySQL', 'NoSQL', 'Redis', 'SQL', '数据采集']/['SQL数据库'], F1=0.5556; bonus TP/FP/FN=[]/['储能', '工业数据处理', '新能源', '物联网']/[], F1=0.0000; education=True
- ANN-0099: title raw=True, normalized=True; skills TP/FP/FN=['Java', 'MySQL', 'Redis', 'Spring Boot', 'Spring Cloud']/['SQL']/[], F1=0.9091; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0100: title raw=True, normalized=True; skills TP/FP/FN=['Java', 'MySQL', 'Redis', 'Spring Boot', 'Spring Cloud', '微服务']/['分布式系统']/[], F1=0.9231; bonus TP/FP/FN=['分布式系统']/[]/[], F1=1.0000; education=True
- ANN-0101: title raw=True, normalized=True; skills TP/FP/FN=['Go', 'Java', 'Python', '后端', '数据库']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=False
- ANN-0102: title raw=False, normalized=True; skills TP/FP/FN=['CSS', 'HTML', 'Java', 'JavaScript', 'MySQL', 'Node.js', 'Python', 'React', 'Vue.js']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0103: title raw=True, normalized=True; skills TP/FP/FN=['Java', 'MyBatis', 'MySQL', 'RESTful API', 'Spring Boot']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0104: title raw=True, normalized=True; skills TP/FP/FN=['CSS3', 'HTML5', 'Java', 'JavaScript', 'Node.js', 'Python', 'React', 'Vue.js', '响应式', '数据库', '跨端适配']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0105: title raw=True, normalized=True; skills TP/FP/FN=['Java', 'MyBatis', 'MySQL', 'RESTful API', 'SQL', 'Spring Boot']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0106: title raw=True, normalized=True; skills TP/FP/FN=['CSS3', 'HTML5', 'JavaScript', 'MySQL', 'PHP', '数据库']/['Laravel', 'ThinkPHP']/[], F1=0.8571; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0107: title raw=True, normalized=True; skills TP/FP/FN=['Java', 'JavaScript', 'MySQL', 'Python', 'Redis', 'Spring Boot']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=False
- ANN-0108: title raw=True, normalized=True; skills TP/FP/FN=['Java', 'MyBatis', 'MySQL', 'Spring Boot', '数据结构']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- ANN-0109: title raw=True, normalized=True; skills TP/FP/FN=['Java', 'MySQL', 'Redis', 'Spring Boot', 'Spring Cloud', '数据库']/['分布式系统', '高并发']/[], F1=0.8571; bonus TP/FP/FN=['分布式系统', '高并发']/[]/[], F1=1.0000; education=True
- ANN-0110: title raw=True, normalized=True; skills TP/FP/FN=['Java', 'MyBatis', 'MySQL', 'SQL', 'Spring Boot']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True

## Lowest three skill-F1 cases

- ANN-0058: skills F1=0.3750; FP=['DL', 'ML', 'RL', '数理统计', '机制', '运筹']; FN=['强化学习', '数学建模', '概率统计', '深度学习']
- ANN-0087: skills F1=0.5455; FP=['Ansible', 'Docker', 'Kubernetes', 'Shell']; FN=['运维脚本']
- ANN-0098: skills F1=0.5556; FP=['Django', 'Flask', 'MySQL', 'NoSQL', 'Redis', 'SQL', '数据采集']; FN=['SQL数据库']

## Main automatically classifiable error types

- model-added skills not in human gold: 78
- possible priority/OR-condition interpretation issue (text marker + set difference): 60
- human-gold skills missed: 49
- skills have both additions and omissions: 40
- title normalization masks a raw-title difference (manual over-normalization check needed): 33
- required/bonus skill mixing: 29

Priority/OR conditions are only flagged when a deterministic text marker co-occurs with a set difference; the current schema does not encode condition logic, so these are review candidates rather than conclusive errors.
