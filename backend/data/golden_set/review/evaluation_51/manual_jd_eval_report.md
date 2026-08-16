# A01 人工 JD 集端到端评测报告

本报告只把 `real_llm_success` 行计入指标；`fallback` 和 `failed` 行保留在逐条结果中，但绝不计入指标。

## 当前真实链路

`JDExtractor.extract` → `LLMProviderChain.extract_structured` → `post_process`。岗位名归一化只用于评测侧的 `normalize_position_name` 对照；`PositionAligner`（Neo4j/SBERT）不在 `JDExtractor.extract` 内。

## 指标

```json
{
  "total_samples": 51,
  "real_llm_success_samples": 51,
  "fallback_samples": 0,
  "failed_samples": 0,
  "title_raw_exact_accuracy": 0.6274509803921569,
  "title_normalized_accuracy": 0.9215686274509803,
  "skills_micro": {
    "tp": 552,
    "fp": 20,
    "fn": 113,
    "precision": 0.965034965034965,
    "recall": 0.8300751879699249,
    "f1": 0.8924818108326596
  },
  "skills_average_sample_f1": 0.8919097946387423,
  "bonus_skills_micro": {
    "tp": 98,
    "fp": 52,
    "fn": 29,
    "precision": 0.6533333333333333,
    "recall": 0.7716535433070866,
    "f1": 0.7075812274368231
  },
  "bonus_skills_average_sample_f1": 0.6515373515373515,
  "education_raw_exact_accuracy": 0.9607843137254902,
  "experience": "Schema coverage gap: current JDExtractionResult schema has no experience_range field",
  "core_duties": "Schema coverage gap: current JDExtractionResult schema has no core_duties field",
  "per_sample_skills_f1": [
    0.8571,
    1.0,
    1.0,
    1.0,
    1.0,
    0.9091,
    0.8,
    0.6667,
    1.0,
    0.9143,
    0.75,
    0.8333,
    0.9615,
    1.0,
    0.8065,
    0.8889,
    0.8571,
    1.0,
    0.6667,
    0.9048,
    1.0,
    0.9697,
    1.0,
    0.875,
    0.7907,
    1.0,
    1.0,
    1.0,
    0.9677,
    0.6154,
    0.9677,
    0.9455,
    0.8889,
    0.9444,
    0.9455,
    0.8235,
    0.8571,
    0.9231,
    0.7143,
    0.7333,
    0.7059,
    1.0,
    0.9333,
    1.0,
    0.8,
    0.9286,
    1.0,
    0.9032,
    0.7692,
    0.9286,
    0.7407
  ],
  "per_sample_bonus_f1": [
    0.0,
    0.8,
    1.0,
    0.8,
    0.4,
    1.0,
    1.0,
    0.0,
    0.0,
    0.75,
    1.0,
    0.3333,
    0.8889,
    1.0,
    0.9231,
    0.0,
    0.8571,
    0.9231,
    0.0,
    0.2857,
    1.0,
    0.7273,
    1.0,
    0.0,
    0.0,
    1.0,
    0.75,
    0.8182,
    1.0,
    0.7778,
    1.0,
    0.8,
    1.0,
    0.0,
    1.0,
    0.7273,
    1.0,
    0.0,
    1.0,
    0.0,
    0.0,
    1.0,
    0.0,
    1.0,
    0.6667,
    0.0,
    1.0,
    1.0,
    1.0,
    1.0,
    1.0
  ],
  "error_types": [
    [
      "human-gold skills missed",
      34
    ],
    [
      "possible priority/OR-condition interpretation issue (text marker + set difference)",
      33
    ],
    [
      "title normalization masks a raw-title difference (manual over-normalization check needed)",
      15
    ],
    [
      "required/bonus skill mixing",
      12
    ],
    [
      "model-added skills not in human gold",
      10
    ],
    [
      "skills have both additions and omissions",
      8
    ]
  ]
}
```

空学历 gold 以 `null / No explicit education requirement` 参与 raw exact 对比：模型同样未输出学历即为正确，凭空输出学历即为错误。经验与核心职责没有对应的 `JDExtractionResult` 字段，属于 Schema coverage gap，不会伪造预测或准确率。`skills` 与 `requirements[nice]` 分别作为必备技能和加分技能的可观测输出；该映射应在后续算法评审中确认。历史 0.6112 未参与本报告。

## Per-JD results

- jd_012: title raw=True, normalized=True; skills TP/FP/FN=['Go', 'Java', 'Python']/['JavaScript']/[], F1=0.8571; bonus TP/FP/FN=[]/['ERP']/[], F1=0.0000; education=True
- jd_030: title raw=True, normalized=True; skills TP/FP/FN=['AIGC', 'Prompt', '数据分析', '数据标注', '视频生成']/[]/[], F1=1.0000; bonus TP/FP/FN=['Python', 'SQL', '数据可视化', '自动化评测']/[]/['多模态模型', '数据标注'], F1=0.8000; education=True
- public_001: title raw=True, normalized=True; skills TP/FP/FN=['Odoo', 'Python']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- public_002: title raw=True, normalized=True; skills TP/FP/FN=['JIRA', 'Java', 'JavaScript', 'Python', 'QC', 'Shell', '自动化测试', '软件测试']/[]/[], F1=1.0000; bonus TP/FP/FN=['大数据测试', '性能测试']/[]/['自动化测试'], F1=0.8000; education=True
- public_003: title raw=True, normalized=True; skills TP/FP/FN=['Linux', '图计算', '数据挖掘', '机器学习', '深度学习', '自然语言处理']/[]/[], F1=1.0000; bonus TP/FP/FN=['Hive', 'PyTorch', 'TensorFlow', '大语言模型']/['互联网风控', '图像', '平台治理', '推荐', '搜索引擎', '智能客服', '用户增长', '计算广告']/['广告算法', '推荐算法', '计算机视觉', '风控算法'], F1=0.4000; education=True
- public_004: title raw=True, normalized=True; skills TP/FP/FN=['Ansible', 'Git', 'Grafana', 'Jenkins', 'Kubernetes', 'Linux', 'Prometheus', 'Python', 'Shell', '可观测性']/['容器化']/['ELK'], F1=0.9091; bonus TP/FP/FN=['AWS', 'Azure', 'GCP', '微服务']/[]/[], F1=1.0000; education=True
- public_005: title raw=True, normalized=True; skills TP/FP/FN=['Office', 'Windows', 'macOS', '故障诊断']/[]/['服务器维护', '网络'], F1=0.8000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- public_006: title raw=True, normalized=True; skills TP/FP/FN=['数据分析', '机器学习', '统计学']/[]/['数据可视化', '数据建模', '项目管理'], F1=0.6667; bonus TP/FP/FN=[]/['CDA', 'CPDA']/[], F1=0.0000; education=True
- public_007: title raw=True, normalized=True; skills TP/FP/FN=['A/B测试', 'Apache Spark', 'Elasticsearch', 'Pandas', 'PyTorch', 'Python', 'SQL', 'TensorFlow', '多模态模型', '大语言模型', '机器学习', '模型评估', '深度学习', '特征工程']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/['多模态模型']/['国产算力研发'], F1=0.0000; education=True
- public_008: title raw=True, normalized=True; skills TP/FP/FN=['C++', 'DeepSeek', 'DeepSpeed', 'LoRA', 'Megatron', 'PyTorch', 'Python', 'Qwen', 'TensorRT-LLM', 'vLLM', '增量预训练', '大语言模型', '深度学习', '自然语言处理', '计算机视觉', '语音交互']/[]/['模型对齐', '模型部署', '模型量化'], F1=0.9143; bonus TP/FP/FN=['ChatBI', '多模态模型', '检索增强生成']/['AGENT']/['Agentic AI'], F1=0.7500; education=True
- public_009: title raw=False, normalized=False; skills TP/FP/FN=['Apache Flink', 'Apache Kafka', 'Apache Spark', 'Flume', 'HBase', 'Hadoop', 'Hive', 'Linux', 'Perl', 'Python', 'Scala', 'Shell']/['AngularJS', 'CSS', 'HTML', 'JavaScript', 'jQuery']/['数据分析', '需求分析', '项目管理'], F1=0.7500; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- public_010: title raw=True, normalized=True; skills TP/FP/FN=['性能调优', '故障处理', '电网业务知识', '系统部署', '问题分析']/[]/['监控', '系统运维'], F1=0.8333; bonus TP/FP/FN=['南方数据中心']/['南方电网基建', '营销', '计量']/['南方电网项目实施'], F1=0.3333; education=True
- r2_001: title raw=True, normalized=True; skills TP/FP/FN=['AJAX', 'Android', 'Axios', 'BOM', 'CSS3', 'DOM', 'ESLint', 'Flex', 'Git', 'Grid', 'HTML5', 'JavaScript', 'Pinia', 'Prettier', 'RESTful API', 'React Native', 'Vite', 'Vue Router', 'Vue.js', 'Vuex', 'Webpack', 'iOS', '响应式布局', '权限控制', '路由配置']/['交互逻辑', '数据渲染']/[], F1=0.9615; bonus TP/FP/FN=['Jest', 'PWA', 'Taro', 'Web安全', 'uni-app', '小程序', '微前端', '数据可视化']/['组件库']/['Web性能'], F1=0.8889; education=True
- r2_002: title raw=False, normalized=True; skills TP/FP/FN=['Docker', 'Go', 'Linux', 'MongoDB', 'PostgreSQL', 'Python', 'Redis', '微服务']/[]/[], F1=1.0000; bonus TP/FP/FN=['AI']/[]/[], F1=1.0000; education=True
- r2_003: title raw=False, normalized=True; skills TP/FP/FN=['AGENT', 'Apache Kafka', 'Chroma', 'Docker', 'Elasticsearch', 'FastAPI', 'Kubernetes', 'LangChain', 'LlamaIndex', 'Milvus', 'PyTorch', 'Python', 'RESTful API', 'Redis', 'SSE', 'TGI', 'Transformer', 'WebSocket', 'vLLM', '向量数据库', '大语言模型', '异步编程', '推理加速', '检索增强生成', '模型量化']/[]/['AWQ', 'DeepSeek', 'Embedding', 'Function Calling', 'GLM', 'GPTQ', 'Hybrid Search', 'Multi-Agent', 'Qwen', 'React', 'Reflexion', 'Tool Calling'], F1=0.8065; bonus TP/FP/FN=['CUDA', 'MindIE', 'OCR', 'Triton', 'VLM', '昇腾CANN']/['Layout Analysis']/[], F1=0.9231; education=True
- r2_004: title raw=True, normalized=True; skills TP/FP/FN=['CTS', 'Perl', 'STA', 'Tcl', '布局布线', '平面规划', '数字后端', '时序分析', '时序收敛', '物理', '物理验证', '电源分析']/[]/['Cadence', 'ICC2', 'Innovus'], F1=0.8889; bonus TP/FP/FN=[]/['Cadence Place和Route']/['DDR', 'PCIE', 'PHY'], F1=0.0000; education=True
- r2_005: title raw=True, normalized=True; skills TP/FP/FN=['ByteTrack', 'C++', 'DeepSORT', 'NCNN', 'ONNX', 'OpenCV', 'PyTorch', 'Python', 'TensorRT', 'YOLO', '去雾', '图像分割', '图像增强', '图像识别', '多目标跟踪', '模型量化', '目标检测', '防抖']/['深度学习', '计算机视觉']/['剪枝', '特征提取', '目标跟踪', '障碍物识别'], F1=0.8571; bonus TP/FP/FN=['SLAM', '视觉定位', '语义分割']/['C++']/[], F1=0.8571; education=True
- r2_006: title raw=True, normalized=True; skills TP/FP/FN=['CSS', 'Element UI', 'HTML', 'Java', 'JavaScript', 'MySQL', 'React', 'SQL', 'Spring Boot', 'Spring Cloud', 'Vue.js']/[]/[], F1=1.0000; bonus TP/FP/FN=['ERP', 'MES', 'Modbus', 'OPC UA', 'Redis', 'WMS']/[]/['消息队列'], F1=0.9231; education=True
- r2_007: title raw=True, normalized=True; skills TP/FP/FN=['Excel', 'PPT', 'SQL', '数据仓库', '数据分析', '数据建模', '数据清洗', '统计学']/['指标体系搭建']/['NumPy', 'Pandas', 'Python', '指标体系', '数仓建模', '数据可视化', '数据透视'], F1=0.6667; bonus TP/FP/FN=[]/['BI报表', 'NumPy', 'Pandas', 'Python', '数据可视化']/['BI', '指标体系', '数据挖掘'], F1=0.0000; education=True
- r2_008: title raw=True, normalized=True; skills TP/FP/FN=['Docker', 'FastAPI', 'LangChain', 'LlamaIndex', 'LoRA', 'Milvus', 'MySQL', 'OCR', 'PyTorch', 'Python', 'QLoRA', 'Redis', 'TensorFlow', 'pgvector', '向量检索', '大语言模型', '提示工程', '检索增强生成', '自然语言处理']/['Agent开发', '重排序']/['AGENT', 'OpenAI API'], F1=0.9048; bonus TP/FP/FN=['SFT微调']/['大模型量化', '显存推理']/['医药行业大模型', '显存', '模型量化'], F1=0.2857; education=True
- r2_009: title raw=True, normalized=True; skills TP/FP/FN=['Java', 'Python', '大语言模型', '自动化测试', '软件测试']/[]/[], F1=1.0000; bonus TP/FP/FN=['LLM测试', 'Transformer', '提示工程']/[]/[], F1=1.0000; education=True
- r2_010: title raw=True, normalized=True; skills TP/FP/FN=['ARM', 'C++', 'EtherCAT', 'Linux', 'Python', 'ROS', 'Twincat', 'X86', '力控', '姿态控制', '导纳控制', '机器人运动学', '路径规划', '轨迹规划', '运动控制', '阻抗控制']/[]/['机器人动力学'], F1=0.9697; bonus TP/FP/FN=['EtherCAT', '医疗机器人', '协作机器人', '手术机器人']/['工业机器人', '理疗机器人']/['TwinCAT'], F1=0.7273; education=True
- r2_011: title raw=True, normalized=True; skills TP/FP/FN=['C++', 'CAN', 'Jetson', 'Linux', 'MAVROS', 'Navigation2', 'OpenCV', 'PCL', 'PX4', 'Python', 'ROS', 'SLAM', 'SPI', 'UART', '传感器融合', '卡尔曼滤波', '激光雷达', '路径规划', '运动控制']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- r2_012: title raw=True, normalized=True; skills TP/FP/FN=['C++', 'CLIP', 'CNN', 'GAN', 'Java', 'OpenCV', 'Python', 'SAM', 'Transformer', '图像处理', '图像生成', '大语言模型', '扩散模型', '模型训练']/[]/['图像分割', '图像分类', '模型轻量化', '目标检测'], F1=0.8750; bonus TP/FP/FN=[]/['大语言模型']/[], F1=0.0000; education=False
- r2_013: title raw=False, normalized=True; skills TP/FP/FN=['C++', 'CNN', 'OpenCV', 'OpenMVS', 'PyTorch', 'Python', 'Transformer', '三维重建', '传感器融合', '多模态数据配准', '多视几何', '影像解译', '摄影测量', '机器视觉', '深度学习', '特征提取', '畸变校正']/[]/['GPU加速', 'LiDAR', 'ORB', 'SFM', 'SIFT', 'SURF', '光束平差', '密集匹配', '相机标定'], F1=0.7907; bonus TP/FP/FN=[]/['GPU加速']/[], F1=0.0000; education=True
- r2_014: title raw=False, normalized=True; skills TP/FP/FN=['Babel', 'CSS3', 'ES6+', 'HTML5', 'JavaScript', 'React', 'Webpack', '前端性能']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- r2_015: title raw=False, normalized=True; skills TP/FP/FN=['Android', 'CSS3', 'HTML5', 'JavaScript', 'Vue.js', 'uni-app']/[]/[], F1=1.0000; bonus TP/FP/FN=['UI设计', 'iOS', '小程序']/['App混合']/['混合'], F1=0.7500; education=True
- r2_016: title raw=True, normalized=True; skills TP/FP/FN=['Apache Kafka', 'Git', 'Gradle', 'Java', 'JavaScript', 'Linux', 'Maven', 'MongoDB', 'MySQL', 'PostgreSQL', 'RabbitMQ', 'Redis', 'RocketMQ', 'SQL', 'Spring Boot', 'Spring Cloud', 'Vue.js', '系统设计']/[]/[], F1=1.0000; bonus TP/FP/FN=['AWS', 'Docker', 'Dubbo', 'Kubernetes', 'gRPC', '分布式', '分库分表', '腾讯云', '阿里云']/['CSS', 'HTML', 'React']/['微服务'], F1=0.8182; education=True
- r2_017: title raw=True, normalized=True; skills TP/FP/FN=['AGENT', 'CI/CD', 'Figma', 'Go', 'Java', 'Python', 'Sketch', 'VLM', '大语言模型', '客户端', '性能监控', '故障处理', '日志分析', '检索增强生成', '知识库构建']/[]/['UI设计'], F1=0.9677; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- r2_018: title raw=True, normalized=True; skills TP/FP/FN=['Git', 'MySQL', 'PostgreSQL', 'Python']/[]/['多线程', '并发编程', '并行计算', '异步编程', '高性能计算'], F1=0.6154; bonus TP/FP/FN=['AWS', 'Azure', 'Django', 'FastAPI', 'Flask', 'GCP', '单元测试']/['Agile', 'SDLC', '契约测试', '集成测试']/[], F1=0.7778; education=True
- r2_019: title raw=True, normalized=True; skills TP/FP/FN=['Apache Kafka', 'Dubbo', 'Java', 'ORM', 'RESTful API', 'RPC', 'RabbitMQ', 'Redis', 'RocketMQ', 'SQL', 'Spring Boot', 'Thrift', '分布式缓存', '本地缓存', '达梦数据库']/[]/['存储过程'], F1=0.9677; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- r2_020: title raw=False, normalized=True; skills TP/FP/FN=['C++', 'Linux', 'NPU推理', 'OCR', 'ONNX', 'OpenCV', 'Python', 'RKNN', '人体姿态估计', '人脸识别', '图像分割', '图像分类', '图像处理', '多模态模型', '少样本学习', '度量学习', '时序建模', '目标检测', '自监督学习', '表情分析', '视频分类', '视频动作检测', '视频异常检测', '视频行为理解', '迁移学习', '重识别']/[]/['嵌入式部署', '模型量化', '目标跟踪'], F1=0.9455; bonus TP/FP/FN=['ISP', 'MNN', 'NCNN', 'OpenVINO', 'TensorRT', 'VLM', '大语言模型', '强化学习']/['LMM', '图像质量评价', '机器人', '相机采集']/[], F1=0.8000; education=True
- r3_001: title raw=False, normalized=True; skills TP/FP/FN=['C', 'C++', 'Java', 'Python']/[]/['数据结构'], F1=0.8889; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- r3_002: title raw=False, normalized=True; skills TP/FP/FN=['AI模型', 'Python', '分子动力学模拟', '反应路径探索', '并行化', '微分方程求解', '数值方法', '数值积分', '数据收集', '机器学习', '机器学习势函数', '构象搜索', '模型搭建', '线性代数求解器', '自由能计算', '蒙特卡洛方法', '量子化学计算']/['优化']/[''], F1=0.9444; bonus TP/FP/FN=[]/['多肽药物', '小分子药物']/[], F1=0.0000; education=True
- r3_003: title raw=False, normalized=True; skills TP/FP/FN=['GIS', 'GeoServer', 'GeoWebCache', 'IO', 'InfluxDB', 'JVM调优', 'Java', 'MongoDB', 'MyBatis', 'MySQL', 'Oracle Spatial', 'PostGIS', 'PostgreSQL', 'RESTful API', 'Redis', 'Spring Boot', 'Spring Cloud', 'TDengine', '叠加分析', '容器化', '数据格式解析', '消息', '空间查询', '空间索引', '缓冲区分析', '集合']/[]/['Hadoop', 'MySQL Spatial', '多线程'], F1=0.9455; bonus TP/FP/FN=['BUFR', 'GRIB', 'GeoServer', 'GeoWebCache', 'HDF5', 'Micaps', 'NetCDF']/[]/[], F1=1.0000; education=True
- r3_004: title raw=False, normalized=True; skills TP/FP/FN=['ECharts', 'JSP', 'React', 'Vue.js', '前端工程化', '原型', '数据可视化']/[]/['ElementUI', 'UI设计', 'VibeCoding'], F1=0.8235; bonus TP/FP/FN=['Docker', 'Figma', 'Kubernetes', '前端性能']/['PS', '安全加固']/['Photoshop'], F1=0.7273; education=True
- r3_005: title raw=True, normalized=True; skills TP/FP/FN=['数据分析', '策略规划', '销售策略']/[]/['市场分析'], F1=0.8571; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- r3_006: title raw=True, normalized=True; skills TP/FP/FN=['Apache Kafka', 'CAP理论', 'Docker', 'Go', 'Java', 'Kubernetes', 'MySQL', 'PostgreSQL', 'Python', 'RESTful API', 'RabbitMQ', 'Redis', 'Spring Boot', 'Spring Cloud', '分布式', '分布式系统', '微服务', '负载均衡']/[]/['Postman', 'Swagger', '性能调优'], F1=0.9231; bonus TP/FP/FN=[]/['文旅']/[], F1=0.0000; education=True
- r3_007: title raw=True, normalized=True; skills TP/FP/FN=['Docker', 'Kubernetes', '向量数据库', '大语言模型', '检索增强生成']/[]/['FAISS', 'Milvus', '推理', '模型微调'], F1=0.7143; bonus TP/FP/FN=['CMG', 'ECLIPSE', 'FLAC3D', 'Petrel']/[]/[], F1=1.0000; education=True
- r3_008: title raw=False, normalized=True; skills TP/FP/FN=['AGENT', 'CAE', 'GNN', 'PINN', '向量检索', '多模态模型', '大语言模型', '数据治理', '有限元仿真', '检索增强生成', '知识图谱']/['代理模型', '优化', '系统架构']/['', 'VLM', '推理', '模型对齐', '模型微调'], F1=0.7333; bonus TP/FP/FN=[]/[]/['CAE仿真', '有限元分析'], F1=0.0000; education=True
- r3_010: title raw=False, normalized=True; skills TP/FP/FN=['AI', 'BDD', 'CI/CD', 'Java', 'Python', 'Selenium']/[]/['Claude', 'Cucumber', 'Cursor', 'Microsoft Copilot', '接口自动化测试'], F1=0.7059; bonus TP/FP/FN=[]/['Java']/[], F1=0.0000; education=True
- r3_011: title raw=False, normalized=False; skills TP/FP/FN=[]/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- r3_012: title raw=False, normalized=True; skills TP/FP/FN=['Airflow', 'Databricks', 'PySpark', 'Python', 'SQL', 'Spark SQL', 'dbt']/[]/['CI/CD'], F1=0.9333; bonus TP/FP/FN=[]/[]/['Azure'], F1=0.0000; education=True
- r3_013: title raw=True, normalized=True; skills TP/FP/FN=[]/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- r3_014: title raw=False, normalized=True; skills TP/FP/FN=['CSS3', 'ECharts', 'ES5', 'HTML5', 'JavaScript', 'Sass', 'TypeScript', 'Vue.js', '小程序', '数据可视化']/['Angular.js', 'React']/['3D建模', 'Git', '虚拟现实'], F1=0.8000; bonus TP/FP/FN=['React', 'TypeScript']/['Angular.js']/['AngularJS'], F1=0.6667; education=True
- r3_015: title raw=True, normalized=True; skills TP/FP/FN=['AWS', 'CI/CD', 'CSS3', 'Cypress', 'Git', 'HTML5', 'JavaScript', 'Jest', 'Next.js', 'Playwright', 'RESTful API', 'React.js', 'TypeScript']/[]/['GitHub', 'React'], F1=0.9286; bonus TP/FP/FN=[]/['Java', 'Python']/[], F1=0.0000; education=True
- r3_016: title raw=True, normalized=True; skills TP/FP/FN=['AJAX', 'CSS3', 'HTML', 'HTML5', 'JavaScript', 'XML']/[]/[], F1=1.0000; bonus TP/FP/FN=['JQueryMobile', 'SenchaTouch', 'iWebkit']/[]/[], F1=1.0000; education=True
- r3_017: title raw=False, normalized=False; skills TP/FP/FN=['AWS', 'Android', 'Azure', 'Detox', 'DevOps', 'GCP', 'Java', 'Jest', 'Kotlin', 'Maestro', 'React Native', 'React Native Testing Library', 'Swift', 'iOS']/[]/['CI/CD', 'Objective-C', '自动化测试'], F1=0.9032; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=False
- r3_018: title raw=False, normalized=True; skills TP/FP/FN=['Java', 'Playwright', 'REST Assured', 'Selenium', 'XPath']/[]/['CI/CD', '接口自动化测试', '自动化测试'], F1=0.7692; bonus TP/FP/FN=['Playwright']/[]/[], F1=1.0000; education=True
- r3_019: title raw=True, normalized=True; skills TP/FP/FN=['Ethernet', 'GPU', 'HDMI', 'I2C', 'Infiniband', 'Linux', 'NVLink', 'PCIe', 'Python', 'SPI', 'Shell', 'USB', 'Windows']/[]/['InfiniBand', '测试自动化'], F1=0.9286; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True
- r3_020: title raw=False, normalized=False; skills TP/FP/FN=['.NET', 'ASP.NET', 'Ansible', 'HL7', 'IoT', 'MVC', 'Python', 'SOA', 'Terraform', 'WCF']/[]/['C#', 'IaC', 'Oracle', 'PL/SQL', 'RESTful API', 'Visual Studio', '机器学习'], F1=0.7407; bonus TP/FP/FN=[]/[]/[], F1=1.0000; education=True

## Lowest three skill-F1 cases

- r2_018: skills F1=0.6154; FP=[]; FN=['多线程', '并发编程', '并行计算', '异步编程', '高性能计算']
- public_006: skills F1=0.6667; FP=[]; FN=['数据可视化', '数据建模', '项目管理']
- r2_007: skills F1=0.6667; FP=['指标体系搭建']; FN=['NumPy', 'Pandas', 'Python', '指标体系', '数仓建模', '数据可视化', '数据透视']

## Main automatically classifiable error types

- human-gold skills missed: 34
- possible priority/OR-condition interpretation issue (text marker + set difference): 33
- title normalization masks a raw-title difference (manual over-normalization check needed): 15
- required/bonus skill mixing: 12
- model-added skills not in human gold: 10
- skills have both additions and omissions: 8

Priority/OR conditions are only flagged when a deterministic text marker co-occurs with a set difference; the current schema does not encode condition logic, so these are review candidates rather than conclusive errors.
