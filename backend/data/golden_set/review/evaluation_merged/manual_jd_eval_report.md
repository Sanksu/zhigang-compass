# A01 人工 JD 集端到端评测报告

本报告只把 `real_llm_success` 行计入指标；`fallback` 和 `failed` 行保留在逐条结果中，但绝不计入指标。

## 当前真实链路

`JDExtractor.extract` → `LLMProviderChain.extract_structured` → `post_process`。岗位名归一化只用于评测侧的 `normalize_position_name` 对照；`PositionAligner`（Neo4j/SBERT）不在 `JDExtractor.extract` 内。

## 指标

```json
{
  "total_samples": 32,
  "real_llm_success_samples": 32,
  "fallback_samples": 0,
  "failed_samples": 0,
  "title_raw_exact_accuracy": 0.84375,
  "title_normalized_accuracy": 0.96875,
  "skills_micro": {
    "tp": 301,
    "fp": 98,
    "fn": 64,
    "precision": 0.7543859649122807,
    "recall": 0.8246575342465754,
    "f1": 0.7879581151832462
  },
  "skills_average_sample_f1": 0.7901899440203304,
  "bonus_skills_micro": {
    "tp": 76,
    "fp": 46,
    "fn": 25,
    "precision": 0.6229508196721312,
    "recall": 0.7524752475247525,
    "f1": 0.6816143497757847
  },
  "bonus_skills_average_sample_f1": 0.4204756181318682,
  "education_raw_exact_accuracy": 1.0,
  "experience": "Schema coverage gap: current JDExtractionResult schema has no experience_range field",
  "core_duties": "Schema coverage gap: current JDExtractionResult schema has no core_duties field",
  "per_sample_skills_f1": [
    0.8571,
    0.2857,
    1.0,
    0.7059,
    1.0,
    0.8696,
    0.7273,
    0.6667,
    0.963,
    0.6857,
    1.0,
    0.9231,
    0.9048,
    0.9412,
    0.6667,
    0.6667,
    0.8,
    0.7586,
    0.6316,
    0.8837,
    0.5455,
    0.8182,
    0.8235,
    0.7692,
    0.3636,
    1.0,
    1.0,
    0.8947,
    0.7619,
    0.9474,
    0.6857,
    0.7391
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
    0.5,
    0.0,
    0.3333,
    1.0,
    1.0,
    0.9231,
    0.0,
    0.6667,
    1.0,
    0.0,
    0.5714,
    1.0,
    0.2857,
    0.0,
    0.0,
    0.0,
    0.0,
    0.75,
    0.75,
    0.0,
    0.875,
    0.0,
    0.8
  ],
  "error_types": [
    [
      "model-added skills not in human gold",
      25
    ],
    [
      "possible priority/OR-condition interpretation issue (text marker + set difference)",
      25
    ],
    [
      "human-gold skills missed",
      20
    ],
    [
      "skills have both additions and omissions",
      18
    ],
    [
      "required/bonus skill mixing",
      9
    ],
    [
      "title normalization masks a raw-title difference (manual over-normalization check needed)",
      4
    ]
  ]
}
```

空学历 gold 以 `null / No explicit education requirement` 参与 raw exact 对比：模型同样未输出学历即为正确，凭空输出学历即为错误。经验与核心职责没有对应的 `JDExtractionResult` 字段，属于 Schema coverage gap，不会伪造预测或准确率。`skills` 与 `requirements[nice]` 分别作为必备技能和加分技能的可观测输出；该映射应在后续算法评审中确认。历史 0.6112 未参与本报告。

## Per-JD results

- jd_012: title raw=True, normalized=True; skills TP/FP/FN=['Go', 'Java', 'Python']/['JavaScript']/[], F1=0.8571; bonus TP/FP/FN=[]/[]/[], F1=0.0000; education=True
- jd_030: title raw=True, normalized=True; skills TP/FP/FN=['数据分析']/['AIGC创作', '数据标注', '评测方案', '音视频质量评估']/['AIGC'], F1=0.2857; bonus TP/FP/FN=['Python', 'SQL', '数据可视化', '自动化评测']/[]/['多模态模型', '数据标注'], F1=0.8000; education=True
- public_001: title raw=True, normalized=True; skills TP/FP/FN=['Odoo', 'Python']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=0.0000; education=True
- public_002: title raw=True, normalized=True; skills TP/FP/FN=['JIRA', 'Java', 'JavaScript', 'Python', 'QC', 'Shell']/['大数据测试', '性能测试', '测试理论', '自动化测试']/['软件测试'], F1=0.7059; bonus TP/FP/FN=['大数据测试', '性能测试']/[]/['自动化测试'], F1=0.8000; education=True
- public_003: title raw=True, normalized=True; skills TP/FP/FN=['Linux', '图计算', '数据挖掘', '机器学习', '深度学习', '自然语言处理']/[]/[], F1=1.0000; bonus TP/FP/FN=['Hive', 'PyTorch', 'TensorFlow', '大语言模型']/['互联网风控', '图像', '平台治理', '推荐', '搜索引擎', '智能客服', '用户增长', '计算广告']/['广告算法', '推荐算法', '计算机视觉', '风控算法'], F1=0.4000; education=True
- public_004: title raw=True, normalized=True; skills TP/FP/FN=['Ansible', 'ELK', 'Git', 'Grafana', 'Jenkins', 'Kubernetes', 'Linux', 'Prometheus', 'Python', 'Shell']/['可观测性', '告警', '日志']/[], F1=0.8696; bonus TP/FP/FN=['AWS', 'Azure', 'GCP', '微服务']/[]/[], F1=1.0000; education=True
- public_005: title raw=True, normalized=True; skills TP/FP/FN=['Office', 'Windows', 'macOS', '故障诊断']/['服务器']/['服务器维护', '网络'], F1=0.7273; bonus TP/FP/FN=[]/[]/[], F1=0.0000; education=True
- public_006: title raw=True, normalized=True; skills TP/FP/FN=['数据分析', '数据建模', '机器学习', '统计学']/['数学', '数据清洗']/['数据可视化', '项目管理'], F1=0.6667; bonus TP/FP/FN=[]/['CDA', 'CPDA']/[], F1=0.0000; education=True
- public_007: title raw=True, normalized=True; skills TP/FP/FN=['A/B测试', 'Apache Spark', 'Elasticsearch', 'Pandas', 'PyTorch', 'Python', 'SQL', 'TensorFlow', '大语言模型', '机器学习', '模型评估', '深度学习', '特征工程']/[]/['多模态模型'], F1=0.9630; bonus TP/FP/FN=[]/['国产算力', '多模态模型', '大语言模型']/['国产算力研发'], F1=0.0000; education=True
- public_008: title raw=True, normalized=True; skills TP/FP/FN=['C++', 'DeepSpeed', 'Megatron', 'PyTorch', 'Python', 'TensorRT-LLM', 'vLLM', '增量预训练', '大语言模型', '模型对齐', '模型部署', '模型量化']/['DeepSeek', 'Qwen', '多模态', '大模型微调', '模型封装', '模型推理', '模型调优', '深度学习', '自然语言处理', '计算机视觉']/['LoRA'], F1=0.6857; bonus TP/FP/FN=['ChatBI', '检索增强生成']/['AGENT', '多模态大模型训练']/['Agentic AI', '多模态模型'], F1=0.5000; education=True
- public_009: title raw=False, normalized=False; skills TP/FP/FN=['AngularJS', 'Apache Flink', 'Apache Kafka', 'Apache Spark', 'CSS', 'Flume', 'HBase', 'HTML', 'Hadoop', 'Hive', 'JavaScript', 'Linux', 'Perl', 'Python', 'Scala', 'Shell', 'jQuery']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=0.0000; education=True
- public_010: title raw=True, normalized=True; skills TP/FP/FN=['性能调优', '故障处理', '电网业务知识', '系统运维', '系统部署', '问题分析']/[]/['监控'], F1=0.9231; bonus TP/FP/FN=['南方数据中心']/['南方电网基建', '营销', '计量']/['南方电网项目实施'], F1=0.3333; education=True
- r2_001: title raw=True, normalized=True; skills TP/FP/FN=['Android', 'Axios', 'CSS3', 'ESLint', 'Flex', 'Git', 'Grid', 'HTML5', 'JavaScript', 'Pinia', 'Prettier', 'RESTful API', 'React Native', 'Vite', 'Vue Router', 'Vue.js', 'Vuex', 'Webpack', 'iOS']/['AJAX', 'BOM', 'DOM', '响应式布局']/[], F1=0.9048; bonus TP/FP/FN=['Jest', 'PWA', 'Taro', 'Web安全', 'Web性能', 'uni-app', '小程序', '微前端', '数据可视化']/[]/[], F1=1.0000; education=True
- r2_002: title raw=False, normalized=True; skills TP/FP/FN=['Docker', 'Go', 'Linux', 'MongoDB', 'PostgreSQL', 'Python', 'Redis', '微服务']/['数据结构']/[], F1=0.9412; bonus TP/FP/FN=['AI']/[]/[], F1=1.0000; education=True
- r2_003: title raw=False, normalized=True; skills TP/FP/FN=['AGENT', 'Apache Kafka', 'Docker', 'FastAPI', 'Kubernetes', 'LangChain', 'LlamaIndex', 'PyTorch', 'Python', 'Redis', 'TGI', 'Transformer', 'vLLM', '检索增强生成', '模型量化']/['Function Calling', 'Multi-Agent', 'RESTful API', 'React', 'Reflexion', 'SSE', 'Tool Calling', 'WebSocket', '向量数据库']/['Chroma', 'DeepSeek', 'Elasticsearch', 'GLM', 'Milvus', 'Qwen'], F1=0.6667; bonus TP/FP/FN=['CUDA', 'MindIE', 'OCR', 'Triton', 'VLM', '昇腾CANN']/['Layout Analysis']/[], F1=0.9231; education=True
- r2_004: title raw=True, normalized=True; skills TP/FP/FN=['Perl', 'Tcl', '布局布线', '时序分析', '物理', '物理验证', '电源分析']/['平面规划', '时序收敛', '脚本语言']/['Cadence', 'ICC2', 'Innovus', '数字后端'], F1=0.6667; bonus TP/FP/FN=[]/['Cadence Place and Route']/['DDR', 'PCIE', 'PHY'], F1=0.0000; education=True
- r2_005: title raw=True, normalized=True; skills TP/FP/FN=['ByteTrack', 'C++', 'DeepSORT', 'NCNN', 'ONNX', 'OpenCV', 'PyTorch', 'Python', 'TensorRT', 'YOLO', '图像分割', '图像增强', '模型量化', '目标检测']/['多目标跟踪', '模型剪枝', '深度学习', '计算机视觉']/['特征提取', '目标跟踪', '障碍物识别'], F1=0.8000; bonus TP/FP/FN=['SLAM', '视觉定位', '语义分割']/['C++', 'NCNN', 'TensorRT']/[], F1=0.6667; education=True
- r2_006: title raw=True, normalized=True; skills TP/FP/FN=['CSS', 'Element UI', 'HTML', 'Java', 'JavaScript', 'MySQL', 'React', 'SQL', 'Spring Boot', 'Spring Cloud', 'Vue.js']/['ERP', 'MES', 'Modbus', 'OPC UA', 'Redis', 'WMS', '消息队列']/[], F1=0.7586; bonus TP/FP/FN=['ERP', 'MES', 'Modbus', 'OPC UA', 'Redis', 'WMS', '消息队列']/[]/[], F1=1.0000; education=True
- r2_007: title raw=True, normalized=True; skills TP/FP/FN=['Excel', 'PPT', 'SQL', '数据分析', '数据建模', '数据清洗']/['指标体系', '统计学']/['NumPy', 'Pandas', 'Python', '数仓建模', '数据可视化'], F1=0.6316; bonus TP/FP/FN=[]/['BI报表', 'NumPy', 'Pandas', 'Python']/['BI', '指标体系', '数据挖掘'], F1=0.0000; education=True
- r2_008: title raw=True, normalized=True; skills TP/FP/FN=['Docker', 'FastAPI', 'LangChain', 'LlamaIndex', 'LoRA', 'Milvus', 'MySQL', 'OCR', 'PyTorch', 'Python', 'QLoRA', 'Redis', 'TensorFlow', 'pgvector', '向量检索', '大语言模型', '提示工程', '检索增强生成', '自然语言处理']/['Agent开发', '模型微调', '重排序']/['AGENT', 'OpenAI API'], F1=0.8837; bonus TP/FP/FN=['SFT微调', '模型量化']/['显存推理']/['医药行业大模型', '显存'], F1=0.5714; education=True
- r2_009: title raw=True, normalized=True; skills TP/FP/FN=['Java', 'Python', '自动化测试']/['功能测试', '安全性测试', '性能测试']/['大语言模型', '软件测试'], F1=0.5455; bonus TP/FP/FN=['LLM测试', 'Transformer', '提示工程']/[]/[], F1=1.0000; education=True
- r2_010: title raw=True, normalized=True; skills TP/FP/FN=['C++', 'Linux', 'Python', 'ROS', '力控', '机器人动力学', '机器人运动学', '轨迹规划', '运动控制']/['姿态控制']/['导纳控制', '路径规划', '阻抗控制'], F1=0.8182; bonus TP/FP/FN=['EtherCAT']/['Twincat']/['TwinCAT', '医疗机器人', '协作机器人', '手术机器人'], F1=0.2857; education=True
- r2_011: title raw=True, normalized=True; skills TP/FP/FN=['C++', 'Jetson', 'Linux', 'MAVROS', 'Navigation2', 'OpenCV', 'PCL', 'PX4', 'Python', 'ROS', 'SLAM', '卡尔曼滤波', '路径规划', '运动控制']/['CAN', 'SPI', 'UART', '多传感器数据融合']/['传感器融合', '激光雷达'], F1=0.8235; bonus TP/FP/FN=[]/[]/[], F1=0.0000; education=True
- r2_012: title raw=True, normalized=True; skills TP/FP/FN=['C++', 'CLIP', 'CNN', 'GAN', 'Java', 'OpenCV', 'Python', 'SAM', 'Transformer', '扩散模型']/['图像生成']/['图像分割', '图像分类', '大语言模型', '模型轻量化', '目标检测'], F1=0.7692; bonus TP/FP/FN=[]/[]/[], F1=0.0000; education=True
- r2_013: title raw=True, normalized=True; skills TP/FP/FN=['C++', 'Python', '三维重建', '传感器融合', '多视几何', '摄影测量']/['光束平差法', '多线程', '性能调优', '机器视觉', '深度学习', '特征匹配', '特征提取', '畸变校准']/['CNN', 'GPU加速', 'LiDAR', 'OpenCV', 'OpenMVS', 'PyTorch', 'SFM', 'Transformer', '光束平差', '密集匹配', '特征提取与匹配', '畸变校正', '相机标定'], F1=0.3636; bonus TP/FP/FN=[]/['GPU加速', '内存', '多线程']/[], F1=0.0000; education=True
- r2_014: title raw=True, normalized=True; skills TP/FP/FN=['Babel', 'CSS3', 'HTML5', 'JavaScript', 'React', 'Webpack', '前端性能']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=0.0000; education=True
- r2_015: title raw=False, normalized=True; skills TP/FP/FN=['Android', 'CSS3', 'HTML5', 'JavaScript', 'Vue.js', 'uni-app']/[]/[], F1=1.0000; bonus TP/FP/FN=['UI设计', 'iOS', '小程序']/['App混合']/['混合'], F1=0.7500; education=True
- r2_016: title raw=True, normalized=True; skills TP/FP/FN=['Apache Kafka', 'Git', 'Gradle', 'Java', 'JavaScript', 'Linux', 'Maven', 'MongoDB', 'MySQL', 'PostgreSQL', 'RabbitMQ', 'Redis', 'RocketMQ', 'SQL', 'Spring Boot', 'Spring Cloud', 'Vue.js']/['性能调优', '消息队列', '系统设计', '缓存']/[], F1=0.8947; bonus TP/FP/FN=['AWS', 'Docker', 'Dubbo', 'Kubernetes', 'gRPC', '分布式', '分库分表', '腾讯云', '阿里云']/['CSS', 'HTML', 'MongoDB', 'React', 'Redis']/['微服务'], F1=0.7500; education=True
- r2_017: title raw=True, normalized=True; skills TP/FP/FN=['AGENT', 'CI/CD', 'Go', 'Java', 'Python', 'VLM', '大语言模型', '检索增强生成']/['性能监控']/['Figma', 'UI设计', '客户端', '知识库构建'], F1=0.7619; bonus TP/FP/FN=[]/['Figma', 'Sketch']/[], F1=0.0000; education=True
- r2_018: title raw=True, normalized=True; skills TP/FP/FN=['Git', 'MySQL', 'PostgreSQL', 'Python', '多线程', '并发编程', '并行计算', '异步编程', '高性能计算']/['API']/[], F1=0.9474; bonus TP/FP/FN=['AWS', 'Azure', 'Django', 'FastAPI', 'Flask', 'GCP', '单元测试']/['Agile', '集成测试']/[], F1=0.8750; education=True
- r2_019: title raw=True, normalized=True; skills TP/FP/FN=['Apache Kafka', 'Dubbo', 'Java', 'ORM', 'RESTful API', 'RabbitMQ', 'Redis', 'RocketMQ', 'SQL', 'Spring Boot', 'Thrift', '达梦数据库']/['RPC', 'Web容器', '分布式缓存', '性能调优', '操作系统', '数据库', '数据结构', '本地缓存', '消息队列', '计算机网络']/['存储过程'], F1=0.6857; bonus TP/FP/FN=[]/[]/[], F1=0.0000; education=True
- r2_020: title raw=False, normalized=True; skills TP/FP/FN=['C++', 'Linux', 'OCR', 'OpenCV', 'Python', '人体姿态估计', '人脸识别', '多模态模型', '少样本学习', '度量学习', '模型量化', '目标检测', '目标跟踪', '自监督学习', '表情分析', '视频动作检测', '迁移学习']/['NPU推理', '内存', '图像处理', '多线程', '模型部署', '目标分割', '缓存']/['ONNX', 'RKNN', '图像分割', '嵌入式部署', '视频行为理解'], F1=0.7391; bonus TP/FP/FN=['ISP', 'MNN', 'NCNN', 'OpenVINO', 'TensorRT', 'VLM', '大语言模型', '强化学习']/['LMM', 'RKNN工具链', '图像质量评价', '相机采集']/[], F1=0.8000; education=True

## Lowest three skill-F1 cases

- jd_030: skills F1=0.2857; FP=['AIGC创作', '数据标注', '评测方案', '音视频质量评估']; FN=['AIGC']
- r2_013: skills F1=0.3636; FP=['光束平差法', '多线程', '性能调优', '机器视觉', '深度学习', '特征匹配', '特征提取', '畸变校准']; FN=['CNN', 'GPU加速', 'LiDAR', 'OpenCV', 'OpenMVS', 'PyTorch', 'SFM', 'Transformer', '光束平差', '密集匹配', '特征提取与匹配', '畸变校正', '相机标定']
- r2_009: skills F1=0.5455; FP=['功能测试', '安全性测试', '性能测试']; FN=['大语言模型', '软件测试']

## Main automatically classifiable error types

- model-added skills not in human gold: 25
- possible priority/OR-condition interpretation issue (text marker + set difference): 25
- human-gold skills missed: 20
- skills have both additions and omissions: 18
- required/bonus skill mixing: 9
- title normalization masks a raw-title difference (manual over-normalization check needed): 4

Priority/OR conditions are only flagged when a deterministic text marker co-occurs with a set difference; the current schema does not encode condition logic, so these are review candidates rather than conclusive errors.
