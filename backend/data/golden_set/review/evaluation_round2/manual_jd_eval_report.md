# A01 人工 JD 集端到端评测报告

本报告只把 `real_llm_success` 行计入指标；`fallback` 和 `failed` 行保留在逐条结果中，但绝不计入指标。

## 当前真实链路

`JDExtractor.extract` → `LLMProviderChain.extract_structured` → `post_process`。岗位名归一化只用于评测侧的 `normalize_position_name` 对照；`PositionAligner`（Neo4j/SBERT）不在 `JDExtractor.extract` 内。

## 指标

```json
{
  "total_samples": 20,
  "real_llm_success_samples": 20,
  "fallback_samples": 0,
  "failed_samples": 0,
  "title_raw_exact_accuracy": 0.25,
  "title_normalized_accuracy": 0.45,
  "skills_micro": {
    "tp": 225,
    "fp": 92,
    "fn": 47,
    "precision": 0.7097791798107256,
    "recall": 0.8272058823529411,
    "f1": 0.764006791171477
  },
  "skills_average_sample_f1": 0.7761534233796232,
  "bonus_skills_micro": {
    "tp": 57,
    "fp": 32,
    "fn": 16,
    "precision": 0.6404494382022472,
    "recall": 0.7808219178082192,
    "f1": 0.7037037037037037
  },
  "bonus_skills_average_sample_f1": 0.47280511885775056,
  "education_raw_exact_accuracy": 0.95,
  "experience": "Schema coverage gap: current JDExtractionResult schema has no experience_range field",
  "core_duties": "Schema coverage gap: current JDExtractionResult schema has no core_duties field",
  "per_sample_skills_f1": [
    0.6667,
    0.9412,
    0.6667,
    0.7273,
    0.8649,
    0.7586,
    0.6316,
    0.8636,
    0.5,
    0.72,
    0.6842,
    0.8148,
    0.6047,
    1.0,
    1.0,
    0.9189,
    0.7826,
    0.9,
    0.6857,
    0.7917
  ],
  "per_sample_bonus_f1": [
    0.7778,
    1.0,
    0.7273,
    0.0,
    0.75,
    1.0,
    0.0,
    0.2857,
    1.0,
    0.7273,
    0.0,
    0.0,
    0.0,
    0.0,
    0.75,
    0.8182,
    0.0,
    0.7778,
    0.0,
    0.8421
  ],
  "error_types": [
    [
      "model-added skills not in human gold",
      18
    ],
    [
      "possible priority/OR-condition interpretation issue (text marker + set difference)",
      15
    ],
    [
      "human-gold skills missed",
      14
    ],
    [
      "skills have both additions and omissions",
      14
    ],
    [
      "required/bonus skill mixing",
      5
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

- r2_001: title raw=False, normalized=False; skills TP/FP/FN=['Axios', 'CSS3', 'ESLint', 'Flex', 'Git', 'Grid', 'HTML5', 'JavaScript', 'Pinia', 'Prettier', 'RESTful API', 'React Native', 'Vite', 'Vue Router', 'Vue.js', 'Vuex', 'Webpack']/['AJAX', 'Android原生', 'BOM', 'DOM', 'iOS原生', '事件机制', '响应式布局', '性能调优', '权限控制', '浏览器渲染机制', '状态', '组件封装', '表单校验', '跨域', '路由配置']/['Android', 'iOS'], F1=0.6667; bonus TP/FP/FN=['Jest', 'PWA', 'Taro', 'Web安全', 'uni-app', '微前端', '数据可视化']/['微信小程序原生', '组件库']/['Web性能', '小程序'], F1=0.7778; education=True
- r2_002: title raw=False, normalized=True; skills TP/FP/FN=['Docker', 'Go', 'Linux', 'MongoDB', 'PostgreSQL', 'Python', 'Redis', '微服务']/['数据结构']/[], F1=0.9412; bonus TP/FP/FN=['AI']/[]/[], F1=1.0000; education=True
- r2_003: title raw=True, normalized=True; skills TP/FP/FN=['Apache Kafka', 'Docker', 'FastAPI', 'Kubernetes', 'LangChain', 'LlamaIndex', 'PyTorch', 'Python', 'Redis', 'Transformer', '检索增强生成', '模型量化']/['向量数据库', '多模态', '推理加速']/['AGENT', 'Chroma', 'DeepSeek', 'Elasticsearch', 'GLM', 'Milvus', 'Qwen', 'TGI', 'vLLM'], F1=0.6667; bonus TP/FP/FN=['CUDA', 'MindIE', 'Triton', '昇腾CANN']/['多模态']/['OCR', 'VLM'], F1=0.7273; education=True
- r2_004: title raw=False, normalized=False; skills TP/FP/FN=['Perl', 'Tcl', '布局布线', '数字后端', '时序分析', '物理', '物理验证', '电源分析']/['平面规划', '时序收敛', '脚本语言']/['Cadence', 'ICC2', 'Innovus'], F1=0.7273; bonus TP/FP/FN=[]/['Cadence Place and Route']/['DDR', 'PCIE', 'PHY'], F1=0.0000; education=True
- r2_005: title raw=False, normalized=False; skills TP/FP/FN=['ByteTrack', 'C++', 'DeepSORT', 'NCNN', 'ONNX', 'OpenCV', 'PyTorch', 'Python', 'TensorRT', 'YOLO', '图像分割', '图像增强', '模型量化', '特征提取', '目标检测', '障碍物识别']/['去雾', '多目标跟踪', '模型剪枝', '防抖']/['目标跟踪'], F1=0.8649; bonus TP/FP/FN=['SLAM', '视觉定位', '语义分割']/['NCNN', 'TensorRT']/[], F1=0.7500; education=True
- r2_006: title raw=False, normalized=False; skills TP/FP/FN=['CSS', 'Element UI', 'HTML', 'Java', 'JavaScript', 'MySQL', 'React', 'SQL', 'Spring Boot', 'Spring Cloud', 'Vue.js']/['ERP', 'MES', 'Modbus', 'OPC UA', 'Redis', 'WMS', '消息队列']/[], F1=0.7586; bonus TP/FP/FN=['ERP', 'MES', 'Modbus', 'OPC UA', 'Redis', 'WMS', '消息队列']/[]/[], F1=1.0000; education=True
- r2_007: title raw=False, normalized=True; skills TP/FP/FN=['Excel', 'PPT', 'SQL', '数据分析', '数据建模', '数据清洗']/['指标体系搭建', '统计学']/['NumPy', 'Pandas', 'Python', '数仓建模', '数据可视化'], F1=0.6316; bonus TP/FP/FN=[]/['BI报表', 'NumPy', 'Pandas', 'Python', '数据建模']/['BI', '指标体系', '数据挖掘'], F1=0.0000; education=True
- r2_008: title raw=True, normalized=True; skills TP/FP/FN=['Docker', 'FastAPI', 'LangChain', 'LlamaIndex', 'LoRA', 'Milvus', 'MySQL', 'OCR', 'PyTorch', 'Python', 'QLoRA', 'Redis', 'TensorFlow', 'pgvector', '向量检索', '大语言模型', '提示工程', '检索增强生成', '自然语言处理']/['Agent开发', '模型微调', '模型推理', '重排序']/['AGENT', 'OpenAI API'], F1=0.8636; bonus TP/FP/FN=['SFT微调']/['大模型量化', '显存推理']/['医药行业大模型', '显存', '模型量化'], F1=0.2857; education=True
- r2_009: title raw=True, normalized=True; skills TP/FP/FN=['Java', 'Python', '自动化测试']/['功能测试', '大模型测试', '安全性测试', '性能测试']/['大语言模型', '软件测试'], F1=0.5000; bonus TP/FP/FN=['LLM测试', 'Transformer', '提示工程']/[]/[], F1=1.0000; education=True
- r2_010: title raw=False, normalized=False; skills TP/FP/FN=['C++', 'Linux', 'Python', 'ROS', '力控', '机器人动力学', '机器人运动学', '轨迹规划', '运动控制']/['ARM', 'Twincat', 'X86', '姿态控制']/['导纳控制', '路径规划', '阻抗控制'], F1=0.7200; bonus TP/FP/FN=['EtherCAT', '医疗机器人', '协作机器人', '手术机器人']/['人工智能', '工业机器人']/['TwinCAT'], F1=0.7273; education=True
- r2_011: title raw=False, normalized=False; skills TP/FP/FN=['C++', 'Jetson', 'Linux', 'MAVROS', 'Navigation2', 'OpenCV', 'PCL', 'PX4', 'Python', 'ROS', 'SLAM', '路径规划', '运动控制']/['CAN', 'CI/CD', 'CMake', 'Catkin', 'DDS', 'Git', 'SPI', 'UART', '多传感器数据融合']/['传感器融合', '卡尔曼滤波', '激光雷达'], F1=0.6842; bonus TP/FP/FN=[]/[]/[], F1=0.0000; education=True
- r2_012: title raw=True, normalized=True; skills TP/FP/FN=['C++', 'CLIP', 'CNN', 'GAN', 'Java', 'OpenCV', 'Python', 'SAM', 'Transformer', '大语言模型', '扩散模型']/['图像生成']/['图像分割', '图像分类', '模型轻量化', '目标检测'], F1=0.8148; bonus TP/FP/FN=[]/[]/[], F1=0.0000; education=False
- r2_013: title raw=False, normalized=False; skills TP/FP/FN=['C++', 'CNN', 'GPU加速', 'LiDAR', 'OpenCV', 'OpenMVS', 'PyTorch', 'Python', 'Transformer', '三维重建', '传感器融合', '多视几何', '摄影测量']/['光束平差法', '内存', '匀光匀色', '多模态数据配准', '多线程', '性能调优', '机器视觉', '深度学习', '特征匹配', '特征提取', '畸变校准']/['SFM', '光束平差', '密集匹配', '特征提取与匹配', '畸变校正', '相机标定'], F1=0.6047; bonus TP/FP/FN=[]/['GPU加速', '内存', '多线程']/[], F1=0.0000; education=True
- r2_014: title raw=False, normalized=False; skills TP/FP/FN=['Babel', 'CSS3', 'HTML5', 'JavaScript', 'React', 'Webpack', '前端性能']/[]/[], F1=1.0000; bonus TP/FP/FN=[]/[]/[], F1=0.0000; education=True
- r2_015: title raw=False, normalized=False; skills TP/FP/FN=['Android', 'CSS3', 'HTML5', 'JavaScript', 'Vue.js', 'uni-app']/[]/[], F1=1.0000; bonus TP/FP/FN=['UI设计', 'iOS', '小程序']/['App混合']/['混合'], F1=0.7500; education=True
- r2_016: title raw=True, normalized=True; skills TP/FP/FN=['Apache Kafka', 'Git', 'Gradle', 'Java', 'JavaScript', 'Linux', 'Maven', 'MongoDB', 'MySQL', 'PostgreSQL', 'RabbitMQ', 'Redis', 'RocketMQ', 'SQL', 'Spring Boot', 'Spring Cloud', 'Vue.js']/['消息队列', '系统设计', '缓存']/[], F1=0.9189; bonus TP/FP/FN=['AWS', 'Docker', 'Dubbo', 'Kubernetes', 'gRPC', '分布式', '分库分表', '腾讯云', '阿里云']/['CSS', 'HTML', 'React']/['微服务'], F1=0.8182; education=True
- r2_017: title raw=False, normalized=False; skills TP/FP/FN=['AGENT', 'CI/CD', 'Go', 'Java', 'Python', 'UI设计', 'VLM', '大语言模型', '检索增强生成']/['性能监控', '组件化UI']/['Figma', '客户端', '知识库构建'], F1=0.7826; bonus TP/FP/FN=[]/['Figma', 'Sketch']/[], F1=0.0000; education=True
- r2_018: title raw=False, normalized=False; skills TP/FP/FN=['Git', 'MySQL', 'PostgreSQL', 'Python', '多线程', '并发编程', '并行计算', '异步编程', '高性能计算']/['性能调优', '数据库']/[], F1=0.9000; bonus TP/FP/FN=['AWS', 'Azure', 'Django', 'FastAPI', 'Flask', 'GCP', '单元测试']/['Agile', 'SDLC', '契约测试', '集成测试']/[], F1=0.7778; education=True
- r2_019: title raw=False, normalized=True; skills TP/FP/FN=['Apache Kafka', 'Dubbo', 'Java', 'ORM', 'RESTful API', 'RabbitMQ', 'Redis', 'RocketMQ', 'SQL', 'Spring Boot', 'Thrift', '达梦数据库']/['RPC', 'Web 容器', '分布式缓存', '性能调优', '操作系统', '数据库', '数据结构', '本地缓存', '消息队列', '计算机网络']/['存储过程'], F1=0.6857; bonus TP/FP/FN=[]/['前端知识']/[], F1=0.0000; education=True
- r2_020: title raw=False, normalized=True; skills TP/FP/FN=['C++', 'Linux', 'OCR', 'ONNX', 'OpenCV', 'Python', 'RKNN', '人体姿态估计', '人脸识别', '多模态模型', '少样本学习', '度量学习', '模型量化', '目标检测', '目标跟踪', '自监督学习', '表情分析', '视频动作检测', '迁移学习']/['NPU推理', '内存', '多线程', '模型部署', '深度学习', '目标分割', '缓存']/['图像分割', '嵌入式部署', '视频行为理解'], F1=0.7917; bonus TP/FP/FN=['ISP', 'MNN', 'NCNN', 'OpenVINO', 'TensorRT', 'VLM', '大语言模型', '强化学习']/['LMM', '图像质量评价', '相机采集']/[], F1=0.8421; education=True

## Lowest three skill-F1 cases

- r2_009: skills F1=0.5000; FP=['功能测试', '大模型测试', '安全性测试', '性能测试']; FN=['大语言模型', '软件测试']
- r2_013: skills F1=0.6047; FP=['光束平差法', '内存', '匀光匀色', '多模态数据配准', '多线程', '性能调优', '机器视觉', '深度学习', '特征匹配', '特征提取', '畸变校准']; FN=['SFM', '光束平差', '密集匹配', '特征提取与匹配', '畸变校正', '相机标定']
- r2_007: skills F1=0.6316; FP=['指标体系搭建', '统计学']; FN=['NumPy', 'Pandas', 'Python', '数仓建模', '数据可视化']

## Main automatically classifiable error types

- model-added skills not in human gold: 18
- possible priority/OR-condition interpretation issue (text marker + set difference): 15
- human-gold skills missed: 14
- skills have both additions and omissions: 14
- required/bonus skill mixing: 5
- title normalization masks a raw-title difference (manual over-normalization check needed): 4

Priority/OR conditions are only flagged when a deterministic text marker co-occurs with a set difference; the current schema does not encode condition logic, so these are review candidates rather than conclusive errors.
