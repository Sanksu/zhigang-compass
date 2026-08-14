"""技能别名词典与白名单。

用于 LLM 抽取后的词典后过滤（设计文档 5.2 节）：
1. SKILL_ALIAS — 将别名/口语化表述映射为标准技能名
2. SKILL_WHITELIST — 标准技能白名单（幻觉防控第三道防线，单一事实源
   configs/skill_whitelist.yaml，设计文档 6.3 节要求 500+ 标准技能）
3. SOFT_SKILL_WHITELIST — 软技能白名单（岗位本体维护，共 20 项，设计文档 9.2 节）
4. SKILL_STOPWORDS — 行业/业务领域词黑名单（防 LLM 幻觉技能入图）
5. normalize_position_name — 岗位名归一化（合并同义重复岗位）
"""

import re
from pathlib import Path

import yaml

# 白名单 yaml 路径（dictionary.py 位于 backend/app/services/extraction/，
# parents[3] = backend/）。与 skill_prerequisites.yaml 的读取口径一致
_SKILL_WHITELIST_PATH = Path(__file__).resolve().parents[3] / "configs" / "skill_whitelist.yaml"

# 内置回退白名单：yaml 缺失/损坏时兜底启动不失败。
# 内容与原硬编码 SKILL_WHITELIST 一致，仅在配置文件缺失时生效。
_FALLBACK_SKILL_WHITELIST: set[str] = {
    "Python", "Java", "JavaScript", "TypeScript", "Go", "Rust", "C++", "C#",
    "Ruby", "PHP", "Swift", "Kotlin", "Scala", "MATLAB", "Shell",
    "C", "SQL", "React", "Vue.js", "Angular", "HTML", "HTML5", "CSS",
    "Webpack", "Vite", "Tailwind CSS", "Next.js", "Nuxt.js", "Bootstrap",
    "ECharts", "Three.js", "数据可视化", "UI设计", "前端工程化",
    "React Native", "jQuery", "ElementUI", "Spring Boot", "Spring Cloud",
    "Django", "Flask", "FastAPI", "Express.js", "Node.js", "ASP.NET",
    "Microservices", "MyBatis", "Scrapy", "RESTful API", "REST", "JSON", "API",
    "MySQL", "PostgreSQL", "MongoDB", "Redis", "Elasticsearch", "SQLite",
    "Oracle", "SQL Server", "Cassandra", "ClickHouse", "NoSQL",
    "Hadoop", "Apache Spark", "Apache Flink", "Apache Kafka", "Hive", "HBase",
    "Airflow", "PySpark", "数据治理", "ETL", "Snowflake", "数据建模", "数据挖掘",
    "Docker", "Kubernetes", "Jenkins", "Git", "GitHub Actions", "Terraform",
    "Ansible", "Prometheus", "Grafana", "CI/CD", "Nginx", "系统运维",
    "AWS", "Azure", "GCP", "Linux", "DevOps",
    "PyTorch", "TensorFlow", "scikit-learn", "大语言模型", "检索增强生成",
    "机器学习", "深度学习", "自然语言处理", "计算机视觉", "数据分析",
    "OpenAI API", "LangChain", "LlamaIndex",
    "大模型算法", "推荐算法", "图像算法", "风控算法", "强化学习", "SLAM算法",
    "广告算法", "AIGC", "多模态模型", "语音识别", "运筹优化算法", "具身智能",
    "机器人", "OpenCV", "嵌入式开发", "自动化测试", "多线程", "三维开发",
    "GIS开发", "大模型评测", "音频标注", "视频标注",
    "AI", "Transformer", "Agentic AI", "Pandas", "NumPy", "Matplotlib",
    "统计学", "Excel", "Tableau", "Power BI", "SAS", "Agile", "Scrum",
    "Maven", "JUnit", "Hibernate", "JDBC", "Core Java", "JIRA",
    "SIEM", "SOAR",
    "团队协作", "沟通能力", "项目管理", "需求分析", "产品设计",
    "问题解决", "逻辑思维", "学习能力", "抗压能力", "时间管理",
    "领导力", "跨部门协作", "创新思维", "客户服务意识", "责任心",
    "主动性", "文档撰写", "汇报能力", "数据分析思维", "执行力",
}


def _load_skill_whitelist() -> dict[str, str]:
    """启动时从 configs/skill_whitelist.yaml 加载 name → category 映射。

    yaml 缺失/解析失败/内容为空时回退内置集，保证启动不失败
    （第三道防线降级为内置集，不阻塞抽取链路；回退集无分类，category 置空串）。
    """
    try:
        data = yaml.safe_load(_SKILL_WHITELIST_PATH.read_text(encoding="utf-8")) or {}
        skills = data.get("skills") or []
        loaded = {
            s["name"]: s.get("category", "")
            for s in skills
            if isinstance(s, dict) and s.get("name")
        }
    except (OSError, yaml.YAMLError):
        return {name: "" for name in _FALLBACK_SKILL_WHITELIST}
    return loaded if loaded else {name: "" for name in _FALLBACK_SKILL_WHITELIST}


# 标准技能白名单（第三道防线，未命中走审核）+ 分类映射（P0-1 落地）。
# SKILL_CATEGORY 由 configs/skill_whitelist.yaml 加载（yaml 缺失回退内置集，category 空串）；
# SKILL_WHITELIST 保持 set API 不变（单一事实源仅 yaml）。
SKILL_CATEGORY: dict[str, str] = _load_skill_whitelist()
SKILL_WHITELIST: set[str] = set(SKILL_CATEGORY)


def skill_category(name: str) -> str:
    """技能名 → 分类；白名单外返回 '未分类'（P0-1，供入图时写入 Skill.category）。"""
    return SKILL_CATEGORY.get(name) or "未分类"


# 技能别名映射：非标准表述 → 标准名
SKILL_ALIAS: dict[str, str] = {
    # 编程语言
    "JS": "JavaScript",
    "TS": "TypeScript",
    "C++": "C++",
    ".net": ".NET",
    # 编程语言口语变体
    "c/c++": "C++",
    "c语言": "C",
    "c#": "C#",
    "golang": "Go",
    # 框架与库
    "spring": "Spring Boot",
    "springboot": "Spring Boot",
    "springcloud": "Spring Cloud",
    "vue": "Vue.js",
    "react": "React",
    "node": "Node.js",
    "express": "Express.js",
    "mybatis": "MyBatis",
    # 大数据
    "hadoop": "Hadoop",
    "spark": "Apache Spark",
    "flink": "Apache Flink",
    "kafka": "Apache Kafka",
    "pyspark": "PySpark",
    # 云原生
    "k8s": "Kubernetes",
    "docker": "Docker",
    # AI/ML
    "pytorch": "PyTorch",
    "tf": "TensorFlow",
    "sklearn": "scikit-learn",
    "numpy": "NumPy",
    "pandas": "Pandas",
    "大模型": "大语言模型",
    "llm": "大语言模型",
    "rag": "检索增强生成",
    "自然语言处理算法": "自然语言处理",
    "深度学习算法": "深度学习",
    "机器学习算法": "机器学习",
    # 数据库
    "sql": "SQL",
    "mysql": "MySQL",
    "pg": "PostgreSQL",
    "redis": "Redis",
    "mongo": "MongoDB",
    "es": "Elasticsearch",
    # 工具
    "git": "Git",
    "jenkins": "Jenkins",
    # P1-1 高频同义变体（评估报告 3.4 别名缺失，归一化到白名单标准词）
    # 前端
    "vue3": "Vue.js",
    "vue2": "Vue.js",
    "reactjs": "React",
    "es6": "JavaScript",
    "element plus": "ElementUI",
    "uniapp": "uni-app",
    "微信小程序": "小程序",
    "小程序开发": "小程序",
    # 后端
    "springmvc": "Spring MVC",
    "mybatis-plus": "MyBatis",
    "mybatis plus": "MyBatis",
    "rest api": "RESTful API",
    "restful": "RESTful API",
    "mq": "消息队列",
    "sql优化": "SQL",
    "sql 优化": "SQL",
    "分布式事务": "分布式",
    "微服务架构": "微服务",
    # AI/ML
    "nlp": "自然语言处理",
    "prompt engineering": "提示工程",
    "prompt工程": "提示工程",
    "prompt 工程": "提示工程",
    "ai agent": "Agentic AI",
    "llm agent": "智能体",
    "agent开发": "Agent开发",
    "sft": "模型微调",
    # 计算机基础
    "面向对象编程": "面向对象",
    "面向对象设计": "面向对象",
    # "算法设计" 别名已移除（JD 基线决策支持 ② 2026-08-12）：指向宽泛词"算法"，
    # 白名单已排除，保留该别名会使基线 alias 扫描仍命中"算法"误报
    "计算机网络基础": "计算机网络",
    # 测试
    "单元测试编写": "单元测试",
    "自动化测试框架": "自动化测试",
    # 软技能
    "沟通协作": "沟通能力",
    "团队协作能力": "团队协作",
    "解决问题能力": "问题解决",
    # 其他
    "性能优化": "性能调优",
    "多传感器融合": "传感器融合",
    "ros2": "ROS",
    "敏捷": "敏捷开发",
    "大屏可视化": "数据可视化",
    # P3 盲审评测词形变体（2026-08-12：故障处置/多模态大模型 等词形差异 FN，
    # 归一化到白名单标准词，与 gold 标注对齐）
    "故障处置": "故障处理",
    "故障排查": "故障处理",
    "多模态大模型": "多模态模型",
    "自动化评测脚本": "自动化评测",
    "AB测试": "A/B测试",
    "ab测试": "A/B测试",
    "可视化分析": "数据可视化",
    # P3b 盲审二轮：标注抽象/词形差异（统一部署→系统部署、电网业务→电网业务知识）
    # 注：不做 "监控管理"→"监控"——"监控"在 SKILL_STOPWORDS（职责词拦截），
    # alias 值会触发 is_noise_skill 别名标准名保护导致停用词失效（TestStopwordInterception）
    "统一部署": "系统部署",
    "电网业务": "电网业务知识",
    "office办公": "Office",
    "office 办公": "Office",
    # P2-B 同义异构归一（岗位评估报告 4.1：AI 编码工具/Agent 生态表述碎片，
    # 统一到白名单标准词，防同一技能建出多个图谱节点）
    "ai coding": "AI辅助编程",
    "ai-assisted coding": "AI辅助编程",
    "ai assisted coding": "AI辅助编程",
    "ai编程": "AI辅助编程",
    "ai辅助编码": "AI辅助编程",
    "ai辅助开发": "AI辅助编程",
    "ai 辅助编程": "AI辅助编程",
    "copilot": "GitHub Copilot",
    "github copilot": "GitHub Copilot",
    "claude code": "Claude Code",
    "cursor": "Cursor",
    "codex": "Codex",
    "chatgpt": "ChatGPT",
    "genai": "GenAI",
    "milvus": "Milvus",
    "dbt": "dbt",
    "databricks": "Databricks",
    "jvm": "JVM",
    ".net core": ".NET",
    "nodejs": "Node.js",
    "postgres": "PostgreSQL",
}

# 工具名别名映射：大小写/拼写变体 → 规范名（P1 Tool 节点碎片治理）。
# 图谱 Tool 节点由 LLM 抽取工具名直接建节点（post_process 仅过 normalize_skill，
# 白名单外工具不统一大小写），同工具因写法不同建出多个节点（如 Ansys/ANSYS、
# DeepSeek/Deepseek）。本表以工具官方名称为准，配合 normalize_tool_name 在
# 抽取侧归一化防复发，cleanup_tools.py 用同一口径清洗存量节点。
# 键统一小写；白名单中的工具词（GitHub/Node.js 等）由 _SKILL_WHITELIST_LOWER 承接。
TOOL_ALIAS: dict[str, str] = {
    # 工业/EDA 工具
    "ansys": "ANSYS",
    "apollo": "Apollo",
    "autoware": "Autoware",
    "calibre": "Calibre",
    "innovus": "Innovus",
    "redhawk": "RedHawk",
    "solidworks": "SolidWorks",
    "nx": "Nx",
    # 云平台/ML 平台
    "aws sagemaker": "AWS SageMaker",
    "sagemaker": "SageMaker",
    "openai": "OpenAI",
    "opencode": "OpenCode",
    "coze": "Coze",
    "harness": "Harness",
    "langfuse": "Langfuse",
    # AI 框架/模型
    "fsdp": "FSDP",
    "deepspeed": "DeepSpeed",
    "megatron": "Megatron",
    "mindspore": "MindSpore",
    "paddlepaddle": "PaddlePaddle",
    "mxnet": "MXNet",
    "mujoco": "MuJoCo",
    "llama": "LLaMA",
    "gtest": "GoogleTest",
    "sglang": "SGLang",
    "pytest": "pytest",
    "intellij idea": "IntelliJ IDEA",
    "idea": "IntelliJ IDEA",
    # 数据/中间件/开发工具
    "datadog": "Datadog",
    "bitbucket": "Bitbucket",
    "docker compose": "Docker Compose",
    "docker-compose": "Docker Compose",
    "geoserver": "GeoServer",
    "ghidra": "Ghidra",
    "go-zero": "go-zero",
    "gorm": "GORM",
    "jboss": "JBoss",
    "kratos": "Kratos",
    "labview": "LabVIEW",
    "loadrunner": "LoadRunner",
    "memcached": "Memcached",
    "n8n": "n8n",
    "npm": "npm",
    "yarn": "yarn",
    "soapui": "SoapUI",
    "sqlalchemy": "SQLAlchemy",
    "tcpdump": "tcpdump",
    "tdengine": "TDengine",
    "thinkphp": "ThinkPHP",
    "weblogic": "WebLogic",
    "websphere": "WebSphere",
    "word": "Word",
    "ragflow": "RAGFlow",
    "rdkit": "RDKit",
    "rviz": "RViz",
    # 开发框架/可视化
    "gazebo": "Gazebo",
    "isaac sim": "Isaac Sim",
    "vue 3": "Vue 3",
    "vue devtools": "Vue DevTools",
    "microsoft copilot": "Microsoft Copilot",
    "neon": "NEON",
    "deepseek": "DeepSeek",
    "dify": "Dify",
    # 防误合并：以下词在 SKILL_ALIAS 被归并（ROS2→ROS、MyBatis-Plus→MyBatis、
    # Element Plus→ElementUI），但工具层面是不同产品/版本，用工具别名优先拦截，
    # 避免 Tool 节点被错误归并
    "ros2": "ROS2",
    "mybatis-plus": "MyBatis-Plus",
    "mybatis plus": "MyBatis-Plus",
    "element plus": "Element Plus",
    "elementui": "ElementUI",
}

# 软技能白名单（岗位本体维护，共 20 项，设计文档 9.2 节）。
# 与 SKILL_WHITELIST 的关系：软技能是其中标记性的子集——JD 侧从正文抽取
# 软技能要求、候选人侧由 LLM 从项目角色/经历推断，均以此清单为唯一枚举域。
# 后缀清洗（clean_skill_name）对该集合内的词整体跳过，避免"项目管理→项目"式退化。
SOFT_SKILL_WHITELIST: frozenset[str] = frozenset({
    "团队协作", "沟通能力", "项目管理", "需求分析", "产品设计",
    "问题解决", "逻辑思维", "学习能力", "抗压能力", "时间管理",
    "领导力", "跨部门协作", "创新思维", "客户服务意识", "责任心",
    "主动性", "文档撰写", "汇报能力", "数据分析思维", "执行力",
})

# 通用软素质噪声词（2026-08-09 新增）：LLM 常把"吃苦耐劳/有责任心/团队精神"等
# 招聘软素质误抽到技术技能 skills 列表。与 SOFT_SKILL_WHITELIST（岗位本体 20 项
# 软技能，保留入图谱）区分：本词表仅用于从 skills 中过滤，不入技能图谱。
# 含子串匹配（"吃苦耐劳"、"责任心强"等变体统一拦截），但须避免误杀技术词——
# 如"可靠性测试/可靠性工程师"是工程词，不在此表（"可靠性"单独列因这类词由
# 上下文区分，故用完整词而非子串）。
SOFT_SKILL_NOISE: frozenset[str] = frozenset({
    "吃苦耐劳", "吃苦", "踏实肯干", "踏实", "肯干", "敬业", "爱岗敬业",
    "有责任心", "责任心强", "工作认真", "认真负责", "严谨细致", "严谨",
    "细心", "耐心", "诚信", "诚实守信", "积极向上", "积极乐观", "乐观",
    "勤奋", "上进", "上进心", "好学", "好学上进", "抗压", "承压",
    "团队精神", "团队合作", "职业素养", "品行端正", "态度端正", "细心负责",
    "稳定性", "责任感", "奉献", "服从安排", "任劳任怨", "勤恳",
})


# 行业/业务领域/招聘福利词黑名单：LLM 在正文缺失的 JD 上常将这些词误抽为技能
SKILL_STOPWORDS: set[str] = {
    "保险", "金融", "银行", "证券", "地产", "零售", "电商", "餐饮", "医疗",
    "教育", "旅游", "物流", "汽车", "能源", "制造", "农业", "传媒", "广告",
    "娱乐", "生活服务", "车联网", "无人机组装测试", "五险", "五险一金", "社保",
    "公积金", "双休", "不加班", "福利", "年终奖", "提成", "股票期权",
    "运营", "销售", "客服", "市场", "行政", "财务", "人力资源", "公关",
    "采购", "前台", "助理岗", "兼职", "实习岗",
    # 碎片/泛词（历史图谱审计残留，正常技能应指向"微服务""软件"的完整语义）
    "微", "软件",
    # P1-2 泛词碎片（评估报告 3.5：JD 高频泛词被 LLM 误抽为技能）
    # 白名单词（操作系统/自动化测试/嵌入式开发/计算机网络 等）已整体保护不受影响
    "系统", "操作", "网络", "前端", "自动化", "嵌入式", "安全", "监控",
    "数据处理",
    # 08-14 盲审迭代：LLM 高频误抽的职责方向词/基础理论词/测试泛词
    # （32 条人工 gold 零出现，纯 FP；白名单词已保护，此处仅停用非白名单词）
    "内存", "数学", "功能测试", "大模型测试", "安全性测试", "多模态测试",
    "测试理论", "模型推理", "模型调优", "预训练模型", "模型剪枝", "指标体系",
    "服务器", "办公设备", "光束平差法", "特征匹配",
    # 08-14 迭代二轮：白名单基础词/上位泛词（gold 口径不收、LLM 高频误抽；
    # 无 _POSITION_SKILL_ROUTING 路由依赖，is_noise_skill 已改为停用词优先）
    "消息队列", "数据结构", "性能调优", "多线程", "缓存", "多模态",
    "操作系统", "计算机网络", "性能测试", "数据库", "模型微调", "模型部署",
}

# 岗位名关键词 → 标准岗位名（合并同义重复岗位，设计文档 4.5 实体对齐的轻量实现）
_POSITION_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    # 细分族前置：优先于通用族/后缀剥离命中，防止真实岗位被剥成碎片
    (("产品经理",), "产品经理"),
    (("项目经理",), "项目经理"),
    (("创始",), "创始工程师"),
    # UI设计师 独立族：置于前端族前，防 "ui" 关键词子串把 UI 岗吸进前端
    # （"UI工程师" 不含 "ui设计师"，仍走前端族）
    (("ui设计师",), "UI设计师"),
    # 安全族置于网络族前，避免"网络安全"被"网络"关键词吸走
    (("安全", "攻防", "渗透", "威胁", "IAM", "SOC"), "网络安全工程师"),
    (("数据科学家",), "数据科学家"),
    (("数据库", "dba", "oracle", "mysql", "postgresql", "snowflake"), "数据库管理员"),
    (("devops", "sre", "站点可靠性", "平台工程", "mlops"), "DevOps工程师"),
    (("架构",), "架构师"),
    (("测试",), "测试工程师"),
    (("运维",), "运维工程师"),
    (("网络",), "网络工程师"),
    (("嵌入式",), "嵌入式开发工程师"),
    # 算法细分族前置（评估报告 P1-A）：方向词明确的算法岗归细分族，
    # 细分失败（纯"算法工程师"）才回退下方通用算法族，防多方向算法技能混聚。
    # 关键词用"方向词+算法"复合词：裸"大模型/多模态/自动驾驶/搜索/视觉"会误吸
    # 非算法岗（LLM应用/多模态理解/自动驾驶系统/搜索运营/视觉设计师），保持停用词判定生效
    (("大模型", "大语言模型", "多模态大模型"), "大模型算法工程师"),
    (("自动驾驶算法", "泊车算法", "vla", "车辆控制", "飞控"), "自动驾驶算法工程师"),
    (("机器视觉", "计算机视觉", "视觉算法", "图像算法"), "机器视觉算法工程师"),
    # 细分族前置：方向词明确的算法岗归细分族。"搜索"为歧义短词（搜索运营/搜索引擎
    # 优化等非算法岗会被子串误吸），改用复合词"搜索算法/搜索工程/搜索推荐"限定
    (("推荐算法", "搜索算法", "搜索工程", "搜索推荐", "检索算法", "增长算法"), "推荐搜索算法工程师"),
    (("语音算法", "asr", "语音识别"), "语音算法工程师"),
    (("slam", "机械臂", "运动规划", "机器人算法"), "机器人算法工程师"),
    (("算法", "推荐", "图像", "视觉", "语音", "NLP", "自然语言", "大模型",
      "深度学习", "机器学习", "人工智能", "风控",
      "ai工程师", "ai 工程师", "ai 工程", "ai agent", "ai 应用"), "算法工程师"),
    (("数据开发", "大数据", "数仓", "ETL", "数据工程"), "大数据开发工程师"),
    (("数据分析", "商业分析", "数据挖掘"), "数据分析师"),
    # "web" 为英文短关键词，子串匹配会误吸 "Web & Mobile Automation Test Engineer"
    # 等英文标题；中文 web 标题（web前端/web开发）已由 "前端" 关键词覆盖，故移除
    (("前端", "h5", "html", "css", "ui"), "前端开发工程师"),
    (("后端", "后台", "服务端"), "后端开发工程师"),
    (("java",), "Java开发工程师"),
    (("python",), "Python开发工程师"),
    (("golang", "go"), "Go开发工程师"),
    (("c/c++", "c++", "c语言"), "C++开发工程师"),
    (("全栈",), "全栈工程师"),
    (("游戏",), "游戏开发工程师"),
    (("硬件",), "硬件工程师"),
    (("软件",), "软件开发工程师"),
    # 失真兜底族：命中后由 _GENERIC_ROUTED_FAMILIES 拦截，按技能路由到细分族
    # （不再作为聚合目的地）；已有细分族（数据分析等）位于其前，优先命中不受影响。
    # "分析师"已拆为细分族（_ANALYST_SUB_FAMILIES），不再走统一兜底族
    (("科学家",), "科学家"),
    (("研究员",), "研究员"),
    (("专家",), "专家"),
    (("顾问",), "顾问"),
    # 海外源验证（2026-08-09）：LLM 对英文长标题抽取的中文碎片归位
    (("ai 研究", "ai研究"), "研究员"),
    (("icam",), "网络安全工程师"),
    # P0 图谱低质岗治理（2026-08-09）：英文复合岗名翻译后的标准族，
    # 前置拦截避免剥后缀成"生化/解决方案/生物光学"等碎片
    (("生化",), "生化工程师"),
    (("生物光学",), "生物光学工程师"),
    (("解决方案",), "解决方案工程师"),
    (("成本估算", "估算", "estimator"), "成本估算师"),
    (("系统可靠性", "可靠性"), "系统可靠性工程师"),
    (("开发者体验",), "开发者体验工程师"),
    (("固件", "firmware"), "嵌入式开发工程师"),
    (("物理设计",), "硬件工程师"),
    # 低频合法岗位白名单（2026-08-12 白名单改造）：剥壳残留必须命中白名单族才入图，
    # 防 LLM 对复杂格式解析不稳产生碎片。以下为审计确认的合法低频岗位，
    # 精确匹配整词（不含子串歧义），避免与既有族冲突
    (("IT系统管理员",), "IT系统管理员"),
    (("产品助理",), "产品助理"),
    (("技术教师",), "技术教师"),
    (("投诉处理助理",), "投诉处理助理"),
    (("计算生物学家",), "计算生物学家"),
    (("首席统计师",), "首席统计师"),
]

# 失真兜底岗位族（2026-08-09 图谱质量治理）："软件/科学家/架构/研究员/顾问/硬件/
# 解决方案/专家/算法"等标题级兜底词把方向各异的 JD 聚合进单一节点，技能集合混聚失真
# （如软件开发工程师 213 技能混 HR/AI/移动/后端；算法工程师 2139 技能混大模型/视觉/
# 机器人/后端基建）。
# 这些族名不再作为聚合目的地：_POSITION_KEYWORDS 中的兜底词仅用于"命中检测"
# （细分族仍在其前优先命中，如"嵌入式软件工程师"→嵌入式开发工程师），命中后由
# normalize_position_name 按 JD 技能路由到 _POSITION_SKILL_ROUTING 的细分族；
# 无技能或未命中路由 → 返回空串不入图（与停用词口径一致）。
_GENERIC_ROUTED_FAMILIES: frozenset[str] = frozenset({
    "软件开发工程师", "科学家", "架构师", "研究员", "顾问",
    "硬件工程师", "解决方案工程师", "专家",
    # 算法工程师（2026-08-09 追加）：通用算法兜底族混聚大模型/视觉/机器人等方向，
    # 纳入技能路由后仅纯通用算法技能（机器学习/深度学习/pytorch 等）仍归本族
    "算法工程师",
})

# 失真兜底岗位族的技能路由表（优先级从细到泛）：核心技能关键词 → 细分岗位族。
# 视觉/自动驾驶/大模型等方向词先于通用算法，避免 "python/机器学习" 把方向明确的
# JD 吸进错误族；语言族放最后（泛语言不抢占 AI/数据/后端）。
# 依据 2026-08-09 真实 JD 模拟（3606 条已抽取：64% 可按技能归位）。
_POSITION_SKILL_ROUTING: tuple[tuple[tuple[str, ...], str], ...] = (
    # 视觉/图像算法（2026-08-09 增强：吸收被通用算法族误收的目标检测/图像处理方向；
    # 再增强：视频/动作/行为识别方向，抽样发现被算法工程师兜底误收）
    (("机器视觉", "计算机视觉", "视觉算法", "图像算法", "opencv", "slam", "目标检测", "图像分割", "图像处理", "ocr",
      "视频分析", "视频处理", "视频识别", "动作识别", "行为识别", "多目标跟踪"), "机器视觉算法工程师"),
    # 自动驾驶（不含通用控制理论词：状态估计/MPC 方向不明确，避免把控制类科学家误吸）
    (("自动驾驶", "泊车", "vla", "车辆控制", "飞控"), "自动驾驶算法工程师"),
    # 大模型（2026-08-09 增强：吸收 NLP/transformer 等被通用算法族误收的方向）
    (("大语言模型", "大模型", "llm", "langchain", "langgraph", "agentic ai", "智能体", "检索增强生成", "llmops", "自然语言处理", "nlp", "transformer", "生成式ai", "aigc"), "大模型算法工程师"),
    # 语音
    (("语音识别", "asr"), "语音算法工程师"),
    # 机器人（2026-08-09 增强：吸收 ROS 技能）
    (("机器人", "模仿学习", "机械臂", "运动规划", "ros"), "机器人算法工程师"),
    # 数据分析/统计（2026-08-09 增强：置于通用算法前，防止"因果推断/双重差分"等统计
    # 建模技能被通用算法族抢走；抽样发现 563 条算法工程师 JD 中 33 条为统计/计量方向）
    (("因果推断", "双重差分", "统计学", "统计建模", "回归建模", "回归分析",
      "ab测试", "a/b测试", "实验设计", "假设检验", "倾向得分", "面板数据", "合成对照"), "数据分析师"),
    # 通用算法
    (("机器学习", "深度学习", "pytorch", "tensorflow", "强化学习", "推荐算法", "搜索算法", "数据挖掘"), "算法工程师"),
    # 大数据
    (("apache spark", "spark", "flink", "kafka", "hadoop", "hive", "hbase", "数仓", "etl", "数据管道", "数据治理", "snowflake", "paimon"), "大数据开发工程师"),
    # 数据分析
    (("数据分析", "tableau", "power bi", "alteryx", "统计学", "统计分析", "a/b测试"), "数据分析师"),
    # 前端
    (("react", "vue", "angular", "css", "html", "html5", "typescript", "webpack"), "前端开发工程师"),
    # 后端
    (("spring boot", "spring cloud", "spring", "node.js", "express", "django", "flask", "fastapi", "微服务", "分布式"), "后端开发工程师"),
    # DevOps
    (("docker", "kubernetes", "k8s", "devops", "ci/cd", "terraform", "jenkins", "ansible"), "DevOps工程师"),
    # 网络安全
    (("网络安全", "渗透", "cybersecurity", "devsecops"), "网络安全工程师"),
    # 测试
    (("测试", "junit", "selenium", "质量保证"), "测试工程师"),
    # 嵌入式
    (("嵌入式", "固件", "firmware", "fpga", "dsp", "rtos", "单片机"), "嵌入式开发工程师"),
    # 数据库
    (("oracle", "mysql", "postgresql", "mongodb", "redis", "elasticsearch"), "数据库管理员"),
    # 语言族（泛语言放最后，避免 python/java 把 AI/数据/后端 JD 吸走）
    (("golang", "go语言"), "Go开发工程师"),
    (("python",), "Python开发工程师"),
    (("c++", "c语言"), "C++开发工程师"),
    (("java",), "Java开发工程师"),
)


# 分析师细分族（方案 C 拆分）：核心词 → 细分岗位名。
# 仅对以"分析师"结尾的岗位名生效（_normalize_base 中优先于后缀剥离判断），
# 避免子串匹配误吸"商业智能工程师""精算师""量化研究员"等非分析师岗位；
# 通用"分析师"（无细分核心词）为兜底，保持原统一族行为。
_ANALYST_SUB_FAMILIES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("量化", "quant"), "量化分析师"),
    (("精算", "actuar"), "精算分析师"),
    (("投资", "invest"), "投资分析师"),
    (("信贷", "贷款", "credit", "mortgage", "抵押"), "信贷分析师"),
    (("保险", "再保险", "insur"), "保险分析师"),
    (("财务", "金融", "财会", "融资"), "财务分析师"),
    (("商业智能", "bi", "intelligence"), "商业智能分析师"),
    (("市场", "market", "marketing"), "市场分析师"),
    (("可持续", "sustain"), "可持续发展分析师"),
    (("业务", "business"), "业务分析师"),
    (("策略", "strategy", "planning"), "策略分析师"),
    (("数据建模", "数据挖掘"), "数据分析师"),
)

# 需保留的技术栈限定词（岗位细分维度）：(匹配串, 显示名)。
# 如 "React前端开发工程师" 与 "前端开发工程师" 分列；web/h5/html 视为通用归并
_TECH_STACKS: tuple[tuple[str, str], ...] = (
    ("小程序", "小程序"), ("鸿蒙", "鸿蒙"), ("移动端", "移动端"), ("移动", "移动"),
    ("桌面", "桌面"), ("可视化", "可视化"), ("大屏", "大屏"),
    ("next.js", "Next.js"), ("three.js", "Three.js"), ("typescript", "TypeScript"),
    ("react", "React"), ("vue", "Vue"), ("angular", "Angular"), ("node", "Node.js"),
    ("echarts", "ECharts"), ("uni-app", "uni-app"), ("taro", "Taro"),
    ("electron", "Electron"), ("webgl", "WebGL"),
)

# 英文岗位名 → 中文标准名（国际源 JD 的 position_name 翻译，再与中文岗位合并去重）
_EN_POSITION_MAP: dict[str, str] = {
    "software engineer": "软件工程师",
    "senior software engineer": "软件工程师",
    "staff software engineer": "软件工程师",
    "software developer": "软件工程师",
    "software developer, experiment controls": "软件工程师",
    "backend software engineer": "后端开发工程师",
    "back end developer": "后端开发工程师",
    "backend developer": "后端开发工程师",
    "backend engineer": "后端开发工程师",
    "frontend engineer": "前端开发工程师",
    "frontend software engineer": "前端开发工程师",
    "front end developer": "前端开发工程师",
    "frontend developer": "前端开发工程师",
    "full stack engineer": "全栈工程师",
    "full-stack engineer": "全栈工程师",
    "fullstack engineer": "全栈工程师",
    "full stack developer": "全栈工程师",
    "data scientist": "数据科学家",
    "data analyst": "数据分析师",
    "senior data analyst": "数据分析师",
    "business analyst": "数据分析师",
    "analytics engineer": "数据分析师",
    "data engineer": "大数据开发工程师",
    "machine learning engineer": "机器学习工程师",
    "ml engineer": "机器学习工程师",
    "machine learning operations engineer": "机器学习工程师",
    "ai engineer": "算法工程师",
    "nlp engineer": "算法工程师",
    "applied scientist": "算法工程师",
    "qa engineer": "测试工程师",
    "software test engineer": "测试工程师",
    "senior quality engineer": "测试工程师",
    "devops engineer": "运维工程师",
    "site reliability engineer": "运维工程师",
    "sre": "运维工程师",
    "network engineer": "网络工程师",
    "network production engineer": "网络工程师",
    "embedded engineer": "嵌入式开发工程师",
    "desktop engineer": "桌面工程师",
    "security engineer": "网络安全工程师",
    "cyber security engineer": "网络安全工程师",
    "quantitative analyst": "量化分析师",
    "quant analyst": "量化分析师",
    "quantitative developer": "量化分析师",
    "product manager": "产品经理",
    "project manager": "项目经理",
    "product engineer": "产品工程师",
    "ui designer": "UI设计师",
    "ux designer": "UX设计师",
    "founding engineer": "创始工程师",
    "founding product engineer": "创始工程师",
    "forward deployed engineer": "现场工程师",
    "customer engineer": "客户工程师",
    # 国际源 Analyst/分析类 → 数据分析师
    "analyst, strategy & analytics": "数据分析师",
    "analytics avp": "数据分析师",
    "applications development technology lead analyst": "数据分析师",
    "business analytics senior analyst": "数据分析师",
    "data analysis manager": "数据分析师",
    "director, planning & analytics": "数据分析师",
    "fsr analyst": "数据分析师",
    "financial & credit analytics analyst": "数据分析师",
    "manager capital markets financial analysis": "数据分析师",
    "model/anlys/valid sr analyst": "数据分析师",
    "senior measurement analyst": "数据分析师",
    "senior statistical analyst": "数据分析师",
    "specialist, hr data, analytics & insights": "数据分析师",
    "sr analyst intl solutions business intelligence": "数据分析师",
    "sr. analyst, marketing analytics": "数据分析师",
    "visualization software and data specialist": "数据分析师",
    # 数据工程类 → 大数据开发工程师
    "data automation engineer": "大数据开发工程师",
    "kafka streaming architect": "大数据开发工程师",
    "snowflake engineer": "大数据开发工程师",
    # 推理优化 → 算法工程师
    "inference engineer, gpu kernel optimization": "算法工程师",
    # 安全/威胁响应 → 网络安全工程师（归一化后并入网络工程师，与 security engineer 一致）
    "advanced cyber threat response & forensics lead/manager": "网络安全工程师",
    "threat context analyst": "网络安全工程师",
    # 软件开发类
    "member of technical staff": "软件开发工程师",
    "principal subsystem engineer": "软件开发工程师",
    "regulatory developer vp": "软件开发工程师",
    "seismic developer": "软件开发工程师",
    # 平台工程 → 运维工程师
    "engineering manager, platform engineering": "运维工程师",
    # 质量工程 → 测试工程师
    "senior supervisor, quality engineering": "测试工程师",
    # 机电/传感器测试 → 嵌入式开发工程师
    "sensor test r&d mechatronics engineer": "嵌入式开发工程师",
    # 射频芯片 → 硬件工程师
    "rfic system engineer": "硬件工程师",
    # P0 方案 1 补充（2026-08-08）：国际源纯英文抽取名正确归位。
    # 注：金融/非技术泛岗（trader/actuary/economist/epidemiologist/quant strategist）
    # 已由 _POSITION_STOPWORDS 拦截（方案 2），此处不映射，避免与停用词冲突。
    "research scientist": "研究员",
    "senior applied scientist": "算法工程师",
    "principal applied scientist": "算法工程师",
    "staff applied scientist": "算法工程师",
    "applied researcher": "研究员",
    "research engineer": "研究员",
    "biostatistician": "生物统计师",
    "consultant": "顾问",
    "solutions architect": "架构师",
    "software architect": "架构师",
    "technical architect": "架构师",
    "solutions engineer": "软件开发工程师",
    "ai/ml applied engineer": "算法工程师",
    "genai engineer": "算法工程师",
    "llm engineer": "算法工程师",
    "ml platform engineer": "算法工程师",
    "platform engineer": "运维工程师",
    "site reliability": "运维工程师",
    "security operations": "网络安全工程师",
    "static timing engineer": "硬件工程师",
    "thermal design engineer": "硬件工程师",
    "mechanical engineer": "硬件工程师",
    "control engineer": "硬件工程师",
    "power engineer": "硬件工程师",
    "firmware engineer": "嵌入式开发工程师",
    "data governance": "大数据开发工程师",
    "data engineering": "大数据开发工程师",
    "backend engineer, ai": "后端开发工程师",
    "test engineer": "测试工程师",
    "database administrator": "数据库管理员",
    "network sre engineer": "运维工程师",
    # P0 图谱低质岗治理（2026-08-09）：英文复合岗名完整映射到标准族，
    # 防止 LLM 抽取"生化/固件/验证"等名词碎片直接入图
    "verification engineer": "测试工程师",
    "verification engineer iii": "测试工程师",
    "quality engineer": "测试工程师",
    "quality assurance engineer": "测试工程师",
    "systems reliability engineer": "运维工程师",
    "reliability engineer": "运维工程师",
    "developer experience engineer": "DevOps工程师",
    "performance engineer": "运维工程师",
    "solution engineer": "解决方案工程师",
    "executive support engineer": "运维工程师",
    "estimator": "成本估算师",
    "physical design engineer": "硬件工程师",
    "avionics engineer": "硬件工程师",
    "aeronautical engineer": "硬件工程师",
    "robotics systems engineer": "机器人算法工程师",
    "bio-chemical engineer": "生化工程师",
    "biochemical engineer": "生化工程师",
    "bio-optics engineer": "生物光学工程师",
    "privacy engineer": "网络安全工程师",
    "web content platform developer": "前端开发工程师",
    "collaboration cloud engineer": "DevOps工程师",
    # P0-B 英文岗位映射扩充（2026-08-12）：审计发现 526 个纯英文技术岗
    # 未映射（654 条记录），归一化后不入图。补齐常见技术岗映射到标准族。
    # 仅收录既有键之外的**新增**键（重复键不写，dict 后值覆盖前值）。
    # 注意最长匹配优先（_translate_en_position 按键长降序匹配），
    # 过短键（如 software engineering）会误匹配 D 级头衔，不收录。
    "software development engineer": "软件开发工程师",
    "software dev engineer": "软件开发工程师",
    "software dev eng": "软件开发工程师",
    "software engineering manager": "软件开发工程师",
    "principal software development engineer": "软件开发工程师",
    "senior software development engineer": "软件开发工程师",
    "software engineering & development": "软件开发工程师",
    "lead director - software development engineering": "软件开发工程师",
    "director of software engineering": "软件开发工程师",
    "engineering manager, software": "软件开发工程师",
    "knowledge engineer manager": "软件开发工程师",
    "knowledge engineer": "软件开发工程师",
    "python developer": "Python开发工程师",
    "senior python developer": "Python开发工程师",
    "staff python engineer": "Python开发工程师",
    "java developer": "Java开发工程师",
    "senior java developer": "Java开发工程师",
    "java software engineer": "Java开发工程师",
    "test automation engineer": "测试工程师",
    "ui developer": "前端开发工程师",
    "ui/ux developer": "前端开发工程师",
    "front end engineer": "前端开发工程师",
    "backend software development engineer": "后端开发工程师",
    "backend development engineer": "后端开发工程师",
    "algorithm engineer": "算法工程师",
    "senior algorithm engineer": "算法工程师",
    "algo developer": "算法工程师",
    "algorithm developer": "算法工程师",
    "business intelligence engineer": "大数据开发工程师",
    "business intelligence developer": "大数据开发工程师",
    "bi developer": "大数据开发工程师",
    "deep learning engineer": "算法工程师",
    "machine learning software engineer": "机器学习工程师",
    "robotics engineer": "机器人算法工程师",
    "robotics/ai motor control scientist": "机器人算法工程师",
    "ai application engineer": "算法工程师",
    "ai applied engineer": "算法工程师",
    "ai automation engineer": "算法工程师",
    "ai inference engineer": "算法工程师",
    "ai systems engineer": "算法工程师",
    "applied ai engineer": "算法工程师",
    "server engineer": "运维工程师",
    "server administrator": "运维工程师",
    "cloud engineer": "运维工程师",
    "cloud system administrator": "运维工程师",
    "cyber security": "网络安全工程师",
    "hardware engineer": "硬件工程师",
    "mechanical design engineer": "硬件工程师",
    "electrical engineer": "硬件工程师",
    "dsp engineer": "硬件工程师",
    "digital signal processing engineer": "硬件工程师",
    "big data engineer": "大数据开发工程师",
    "data platform engineer": "大数据开发工程师",
    "full stack software engineer": "全栈工程师",
    "fullstack developer": "全栈工程师",
}

# 合法岗位白名单（2026-08-12 白名单改造）：剥壳残留核心词必须命中本集合才返回，
# 否则返回空串不入图。覆盖所有关键词族 standard + 技能路由细分族 + 分析师细分族 +
# 英文翻译结果（"软件工程师"等翻译中间态也合法，走后续关键词族映射）。
# 刻意排除 _GENERIC_ROUTED_FAMILIES（失真兜底族不作为残留返回，只按技能路由）。
_POSITION_WHITELIST: frozenset[str] = frozenset(
    {standard for _, standard in _POSITION_KEYWORDS}
    | {family for _, family in _POSITION_SKILL_ROUTING}
    | {standard for _, standard in _ANALYST_SUB_FAMILIES}
    | set(_EN_POSITION_MAP.values())
)

# 无信息量泛岗位词：归一化结果命中时视为空岗位（不入图）。
# 后 4 行为评估报告 P0-1 新增：真实岗位被剥后缀后残留的无信息量核心词
# （真实族由 _POSITION_KEYWORDS 前置拦截，不会命中这些词）。
# 公司名停用词（审查报告 P0-1：LLM 把公司名当岗位名）。
# 单独列出：仅在"整个岗位名就是公司名"时拦截（_is_stopword_blocked），
# 避免误伤 "Google工程师" 这类 公司名+岗位词 的合法岗位——归一化剥掉后缀后
# 核心词为公司名时不应拦截（2026-08-11 回归修复）。
_COMPANY_NAME_STOPWORDS: frozenset[str] = frozenset({
    "Amazon", "Amazon.com", "Apple", "Avantor", "Binance", "Deloitte",
    "Google", "JPMorganChase", "Microsoft", "NVIDIA", "Nex", "Nomura",
    "Novartis", "Point72", "Raytheon", "Ripple", "Starbucks", "TYCHON", "Verse",
})


# 末组为岗位评估报告 P0-A 新增：碎片/业务词岗位（LLM 误抽业务词/碎片岗位名，
# 全部为低频空岗，归一化原样返回），加停用词使其不再入图
_POSITION_STOPWORDS: set[str] = {
    "技术", "开发", "工程师", "管理", "专员", "前台", "文员",
    "产品", "项目", "数据", "客户", "研究", "知识", "系统", "工程",
    "结构", "数字", "运营", "平台", "安全", "投资", "风险", "控制",
    "计划", "需求", "精算", "销售", "客服", "招聘", "网页", "客户端",
    "逆向", "集成", "空间", "机械", "电气", "现场", "部署", "工艺",
    "定价", "服务器", "经理", "创始", "董事总", "分析",
    # P0-A 碎片/业务词岗位（低频空岗，不入图）
    "专利", "传播", "跟单员", "量化", "中训练", "后训练", "前向部署",
    "大客户销售", "定制服装导购", "短视频编导", "项目申报销售", "电子发现协调员",
    "设施合同与投标", "MEC运营", "OSINT 情报收集员", "Palantir 前向部署",
    "产品交付", "信贷支持", "数据生产", "零售运营分析", "多模态理解",
    "应用研究", "廉政审计", "报表分析", "桥梁设计", "特效工具", "自动驾驶系统",
    # P2 图谱清理新增：存量脏岗位（rebuild_graph 后仍存在的低频边）。
    # 工具/平台名误抽（LLM 把工具/平台/技术概念当岗位名）
    "LLMOps平台", "IMS核心网", "AEM 解决方案", "CMDB 发现", "FAE现场应用",
    "Palantir 管理员", "Kubernetes 服务", "Azure 云", "ML平台", "SCADA迁移与集成",
    "Genesys CCaaS", "SAP BTP", "EDC", "WCS", "DFT", "Power Automate", "KDB",
    "BI", "Angular/NodeJS", "智能体平台", "爬虫", "提示词", "物联网",
    "GRC 自动化", "云系统管理员", "AS400 应用程序", "AS400",
    # 碎片/业务词误抽（工作内容/领域词被抽成岗位，非真实业务岗）
    "应用程序", "商业智能", "安卓", "构建与发布", "工业", "技术支持", "自动化",
    "智能化", "自动化集成", "商业智能与平台管理", "数据处理", "控制系统",
    "设计验证", "物理验证", "数字信号处理", "模拟电路设计", "静态时序", "热设计",
    "热流体仿真", "热工流体仿真", "显示技术", "显示电气设计", "载荷与动力学",
    "机械设计", "天线系统", "相机控制系统标定", "仿真与渲染", "质量工程",
    "成本管理", "收益管理", "站点运营", "现场服务", "指定支持", "技术客户",
    "技术产品", "技术项目", "技术业务伙伴", "产品解决方案", "客户解决方案",
    "客户策略分析", "数据验证风险", "包裹洞察与定价", "战略财务", "财务运营",
    "私募市场二级数据", "制造与系统协同设计工作流", "全球品类采购", "品类采购",
    "大客户", "市场通路TM", "增长运营", "产品运营", "独立站运营", "APP数据运营",
    "社交媒体", "社区垂类运营", "销售支持", "资源管理岗", "组合管理岗",
    "云服务解决方案中级助理", "数字渠道分析高级助理", "数据平台总监", "人力分析总监",
    "客户数据科学总监", "临床情报总监", "神经病学分析副总监", "临床知识交付",
    "空间组学", "生物信息学", "学习促进师", "编程教师", "STEM 讲师", "IT技术讲师",
    "课程导师", "数学/统计学辅导讲师", "技术总监", "数据录入", "数据录入分析",
    # 未翻译英文岗（抽取后未过英文映射，低频脏边，待 P0-B 映射扩充后再处理）
    "Corporate Vice President - Head of Enterprise AI Platform",
    "Director, Supply Chain Strategy & Analytics",
    "Executive Director - North America Delta 1 Flow Swaps Trading",
    "Measurement Science Partner, Global Accounts",
    "Tenant Relocation Specialist",
    "Web & Mobile Automation Test Engineer",
    "Feature Lead - Technology",
    "Manager, Logistics",
    "Legal Technology & Contract Management Systems Administrator",
    "AI Infra Engineer",
    # P0-B 移除（2026-08-12）：该岗位已入 _EN_POSITION_MAP（→算法工程师），
    # 停用词会抢先拦截使映射失效
    "AI 业务自动化", "机电一体化", "密码应用", "AR/VR 设计验证",
    "Gemini App 合作伙伴", "交通规划", "智能驾驶路测",
    # 问题 1 修复：搜索短词改为复合词限定后，运营/SEO 类岗位不再被算法族吸走，
    # 加停用词使其不入图（P0-A 碎片岗位同类处理）
    "搜索运营", "搜索引擎优化",
    # P0 图谱归一化评估新增（2026-08-08）：泛词/非技术岗/招聘形态低质岗，
    # 归一化原样返回且低频，加停用词拦截。细分分析师岗（量化/业务/市场…）
    # 由 _ANALYST_SUB_FAMILIES 前置拆分，不受"分析师"兜底拦截影响。
    "分析师", "程序员", "行政管理", "人力资源", "业务分析",
    "货代销售", "商业水电维修工", "一级建造师", "研究助理", "项目助理",
    "副总裁，量化策略师", "人事行政助理", "材料实验室行政", "综合行政",
    "行政管理主管", "人力资源经理",
    # P0 方案 2 清理防复发（2026-08-08）：非技术/金融/工具泛岗，
    # 图谱低频单例已删，停用词拦截防 rebuild 复发。
    # 注：可持续发展分析师为 _ANALYST_SUB_FAMILIES 细分岗，不在拦截范围
    "交易员", "经济学家", "流行病学家", "精算师", "量化策略师",
    "Guidewire", "可视化软件开发工程师", "桌面",
    # 海外源验证（2026-08-09）：LLM 把 Application Developer 抽成"应用"碎片
    "应用",
    # P0 图谱低质岗治理（2026-08-09）：LLM 抽取的英文复合岗名词碎片，
    # 无标准岗位语义，拦截不入图（完整岗位名由 _EN_POSITION_MAP/_POSITION_KEYWORDS 承接）
    "验证", "质量", "交付", "解决方案", "隐私", "估算师", "信息化",
    "生化", "固件", "生物光学", "系统可靠性", "开发者体验", "性能工程",
    "定价分析", "消费者洞察", "洞察与创新", "音乐洞察与版税", "核心投资组合",
    "治疗领域分析与洞察", "商业抵押贷款组合管理总监", "抵押贷款组合管理总监",
    "资本市场财务分析", "资金管理", "量化风险与投资组合分析", "材料与技术合规",
    "供应链采购与履约", "战略收入管理", "规模化业务增长", "劳动力分析总监",
    "Web 内容平台", "产品内容", "任务主管系统", "协作云", "前向部署分析",
    "前向部署洞察", "航空", "航空电子飞行仪表", "蜂窝4G/5G系统性能",
    "机器人系统", "电商小程序", "数据科学", "高管支持", "商业智能助理",
    "Kotlin 质量保证", "物理设计", "Adobe转型高级助理",
    # P0 图谱重建新增（2026-08-09）：回刷 title 归一化暴露的碎片/非技术岗/含地点/公司残留
    "副总裁", "子系统", "人力资源经理/", "招聘专员", "大客户销售", "增长策略运营",
    "全国重点客户", "应用开发岗", "政务AI应用", "激光高级工艺", "蜂窝系统性能",
    "理工科课程导师", "社区用户", "商业化运营", "C开发工程师-南京",
    "南京天虞科技有限公司-技术", "客户端开发-双休-快手", "wcs开发工程师会有出差",
    "500强外资市场通路TM", "一级建造师市政+", "结构工程师", "高级社交媒体",
    "短视频编导", "大客户销售-上海", "SAP业务技术平台", "DevSecOps",
    "招聘专员-广州", "社区用户&商业化运营",
    "结构工程师 / 遥控器结构设计工程师 / 高级结构",
    "Senior Social Media Manager 高级社交媒体", "短视频编导-小红书方向",
    # 审查报告 P0-1 清理防复发（2026-08-11）：公司名当岗位名由
    # _COMPANY_NAME_STOPWORDS 承接（见上，此处 union 注入，保持整体拦截语义）
    # 审查报告 P0-1 清理防复发（2026-08-11）：LLM 把岗位名剥壳成碎片词，
    # 全部为低频单例且无标准关键词族承接（"税务"拦"税务经理"、"通信系统"
    # 拦"通信系统工程师"——这些变体也不命中任何关键词族，本就是非标准碎片）
    "AI 智能体", "AS400 应用", "Palantir前向部署", "Station Operations Specialist 站点运营",
    "仪器设计", "任务评估研究", "创客教育", "大型机应用", "生物工艺应用",
    "税务", "通信系统", "分析工程", "人力资源技术与人才分析副总裁助理",
    # zhilian 回填重抽副作用（2026-08-12）：提示词兜底规则让空岗位名恢复的同时，
    # LLM 对复杂格式解析不稳定，产出剥壳碎片/公司名当岗位名。全部低频单例且
    # 无标准关键词族承接，拦截不入图（"人事"拦"人事经理"剥壳、"公司：外企德科"
    # 是公司名、"智能"拦"智能开发工程师AI"剥壳）
    "人事", "智能", "激光工艺", "重点客户", "AI平台", "AI总监", "公司：外企德科", "行政",
} | set(_COMPANY_NAME_STOPWORDS)


def _is_stopword_blocked(word: str, original: str) -> bool:
    """停用词拦截判断：中文/业务词碎片剥后缀后仍拦截；纯英文公司名仅在
    「整个岗位名就是公司名」时拦截（公司名+岗位词如 "Google工程师" 剥后缀
    残留公司名不算，仍走中文规则路径）。
    """
    if word not in _POSITION_STOPWORDS:
        return False
    if word in _COMPANY_NAME_STOPWORDS:
        return word == original
    return True


# 岗位名前缀修饰词（级别/招聘形态），归一化时去除
_POSITION_PREFIX_RE = re.compile(r"^(初级|中级|高级|资深|专家|助理|实习|见习|应届|研发)")
# 岗位名后缀（含"开发/技术员/程序员"等变体），归一化时去除后按关键词重映射。
# 科学家/研究员/专家/顾问 为低频同类岗的统一族后缀（由兜底关键词族承接，
# 避免剥后缀后产生"财务/业务"等碎片）；"分析师"由细分族（_ANALYST_SUB_FAMILIES）
# 在剥后缀前拦截，不走此兜底；"高级"用于剥离尾部级别词（如"DevOps高级"）
_POSITION_SUFFIX_RE = re.compile(
    r"(工程师|技术员|程序员|研发人员|研发|开发|设计师|经理|主管|负责人|专员|"
    r"分析师|科学家|研究员|专家|顾问|高级)$"
)


# 纯 ASCII 字母数字关键词（go/ui/java/dba 等短词）：子串匹配会误吸 google/goods/guidance
# 等含词内子串的名，统一改词边界匹配（ASCII \w 边界）；含符号/空格的关键词保持子串匹配
_ASCII_WORD_RE = re.compile(r"^[a-z0-9]+$")


def _keyword_hit(low: str, kw: str) -> bool:
    """关键词匹配：纯 ASCII 词用词边界，其余（中文/含符号）子串匹配。"""
    k = kw.lower()
    if _ASCII_WORD_RE.match(k):
        return bool(re.search(r"(^|[^a-z0-9])" + re.escape(k) + r"($|[^a-z0-9])", low))
    return k in low


def _normalize_base(name: str) -> str:
    """基础岗位名归一化：循环去后缀至核心词，再按关键词族映射。"""
    core = name
    while core:
        low = core.lower()
        for keywords, standard in _POSITION_KEYWORDS:
            if any(_keyword_hit(low, k) for k in keywords):
                return standard
        # 分析师细分族：岗位名以"分析师"结尾 → 查细分映射（量化/财务/信贷…），
        # 命中返回细分标准名，否则兜底"分析师"。仅限"分析师"结尾（方案 C），
        # 避免子串匹配误吸"商业智能工程师""精算师""量化研究员"等非分析师岗位
        if core.endswith("分析师"):
            stem = core[:-3].strip().lower()
            for keywords, standard in _ANALYST_SUB_FAMILIES:
                if any(k.lower() in stem for k in keywords):
                    return standard
            return "分析师"
        next_core = _POSITION_SUFFIX_RE.sub("", core).strip()
        if next_core == core or not next_core:
            break
        core = next_core
    # 剥后缀后残留核心词再校验（评估报告 P0-1）：防止"产品经理"剥成
    # "产品"、"董事总经理"剥成"董事总"等碎片直接入图；
    # 白名单改造（2026-08-12）：纯中文残留核心词必须命中合法岗位白名单才返回，
    # 否则视为剥壳碎片返回空串（防 LLM 解析不稳产生新碎片，如"人事"/"智能"）。
    # 含非中文的残留（如"Google工程师"剥壳残留"Google"、"LLM应用"）维持原语义
    # 保留——公司名+岗位词、技术缩写+岗位词组合是设计允许的合法岗位
    result = core or name
    if _is_stopword_blocked(result, name):
        return ""
    if not re.search(r"[A-Za-z0-9]", result) and result not in _POSITION_WHITELIST:
        return ""
    return result


# CJK 检测：含中文的岗位名不执行英文子串翻译（评估报告 P0-2），
# 防混合标题内嵌缩写（SRE/BI/EDC 等）被英文映射劫持
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _translate_en_position(name: str) -> str | None:
    """英文岗位名 → 中文标准名。

    匹配优先级：精确 → 逗号前主标题 → 最长子串（单词边界，容忍逗号/连字符）。
    """
    if _CJK_RE.search(name):
        return None
    low = name.strip().lower()
    if low in _EN_POSITION_MAP:
        return _EN_POSITION_MAP[low]
    head = low.split(",")[0].strip()
    if head in _EN_POSITION_MAP:
        return _EN_POSITION_MAP[head]
    for en, zh in sorted(_EN_POSITION_MAP.items(), key=lambda x: -len(x[0])):
        if re.search(r"(^|[^a-z])" + re.escape(en) + r"($|[^a-z])", low):
            return zh
    return None


def _route_position_by_skills(skills: list[str] | None) -> str:
    """失真兜底岗位按技能路由到细分族；无技能或未命中返回空串（不入图）。

    技能名先经 normalize_skill 归一（别名/白名单统一），再小写匹配路由关键词，
    与抽取/聚合链路的技能口径一致。
    """
    if not skills:
        return ""
    norm = [normalize_skill(s).lower() for s in skills if isinstance(s, str) and s]
    for keywords, family in _POSITION_SKILL_ROUTING:
        for kw in keywords:
            if any(_keyword_hit(s, kw) for s in norm):
                return family
    return ""


def normalize_position_name(name: str, skills: list[str] | None = None) -> str:
    """岗位名归一化：英文翻译中文 + 合并同义重复岗位，保留技术栈细分维度。

    skills 为 JD 已抽取技能名列表，仅对失真兜底族生效（见步骤 5）。

    步骤：
    1. 英文岗位名翻译为中文（"Software Engineer" → "软件工程师"），与中文岗位合并去重
    2. 整体处理（不再按分隔符取第一段）：混合标题如 "后/前端开发、测试" 会在
       整串中命中关键词族（"前端"）归入主族，避免产生 "后" 这类碎片岗位
    3. 提取技术栈限定词（括号内 "前端开发工程师(React)" 或前缀 "React前端开发工程师"），
       React/Vue/小程序/鸿蒙等作为细分岗位保留，web/h5/html 视为通用归并
    4. 基础岗位名归一化（去级别前缀、循环去后缀、关键词族映射）；
       归一化结果为泛词（"技术"/"后台" 等无信息量）时返回空串，不入图
    5. 失真兜底族（软件开发工程师/科学家/架构师/研究员/顾问/硬件工程师/
       解决方案工程师/专家/算法工程师）不再作为聚合目的地：按 skills 技能内容路由到
       细分族，无技能或未命中路由返回空串，不入图（2026-08-09 图谱质量治理）
    6. 技能词不入图：归一化结果命中技能白名单时视为"技能被抽成岗位"，
       返回空串（评估报告 P1，防 SQL/C/FPGA/PyTorch 等技术名词污染岗位图）

    示例：
    - "Software Engineer" / "Senior Software Engineer" → ""（失真兜底族，无技能不入图）
    - "前端开发" / "web前端开发工程师" → "前端开发工程师"
    - "React前端开发工程师" / "前端开发工程师(React)" → "React前端开发工程师"
    - "软件开发工程师"(skills=["Python", "Django"]) → "后端开发工程师"
    - "技术" → ""（泛词不入图）
    - "SQL" → ""（技能词不入图）
    """
    translated = _translate_en_position(name)
    # 实习类岗位不入图（招聘形态，非正式岗位族；含"实习"即过滤，含翻译结果）
    if "实习" in (translated or name):
        return ""
    if translated:
        # 翻译结果再过中文归一化，确保与中文路径岗位名统一（如"软件工程师"→"软件开发工程师"）
        base = _normalize_base(translated)
        # 失真兜底族不再作为聚合目的地：按技能路由，无技能/未命中 → 不入图
        if base in _GENERIC_ROUTED_FAMILIES:
            return _route_position_by_skills(skills)
        if not base or _is_stopword_blocked(base, translated):
            return ""
        result = base
    else:
        paren = re.search(r"[（(]([^()（）]*)[)）]", name)
        paren_tech = paren.group(1).strip() if paren else ""
        base = re.sub(r"[（(].*?[)）]", "", name).strip()
        base = _POSITION_PREFIX_RE.sub("", base).strip()

        # 前缀技术栈提取（"React前端开发工程师" → tech=React, base=前端开发工程师）
        tech = ""
        low = base.lower()
        for match, display in sorted(_TECH_STACKS, key=lambda t: len(t[0]), reverse=True):
            if low.startswith(match):
                tech = display
                base = base[len(match):].strip()
                break

        base_raw = base  # 提取 tech 后、归一化前的原始 base（tech 细分保底用）
        base = _normalize_base(base)
        # 失真兜底族不再作为聚合目的地：按技能路由，无技能/未命中 → 不入图
        if base in _GENERIC_ROUTED_FAMILIES:
            return _route_position_by_skills(skills)
        if not base or _is_stopword_blocked(base, name):
            if not tech:
                return ""
            # 技术栈细分岗位（鸿蒙/桌面/移动…）：base 剥到泛词（开发/工程师）
            # 不算失败，用 tech + 原始 base 保底，避免整个岗位被丢弃
            result = tech + base_raw
        elif tech:
            result = tech + base
        else:
            result = base
            if paren_tech:
                # 括号内技术栈限定（"前端开发工程师(React)" → React前端开发工程师）
                for match, display in _TECH_STACKS:
                    if paren_tech.lower() == match:
                        result = display + base
                        break
    # 技能词不入图：归一化结果命中技能白名单（大小写不敏感）→ 技能被抽成岗位
    # 停用词再查最终 result：tech 前缀拆分后的完整岗位名（如 "Angular/NodeJS"
    # 拆成 tech=Angular + base=/NodeJS）需以组合结果整体拦截
    if _is_stopword_blocked(result, name) or _SKILL_WHITELIST_LOWER.get(result.lower()):
        return ""
    if not translated and not _CJK_RE.search(name):
        # 纯英文未翻译岗位（问题 2）：多词英文长标题未识别 → 不入图；
        # 单英文词（RAG/SRE 等缩写或技术词）原样保留，discovery 等下游依赖其作为岗位名
        if result.lower() == name.strip().lower() and " " in name.strip():
            return ""
    return result


# 技能名尾随修饰词（"MySQL 优化"→"MySQL"、"K8s 运维"→"K8s"）。未命中别名/白名单
# 时先剥修饰词再重查，命中才采用，避免"技能+修饰词"组合分裂成独立图谱节点。
# 与 post_processor.SUFFIXES 共用一套词表，保证抽取与消费链路口径一致。
_SKILL_MODIFIERS = {
    "中间件", "产品", "协议", "工具", "工程师", "平台", "应用", "开发", "引擎",
    "技术", "接口", "方案", "架构", "标准", "框架", "算法", "管理", "系统",
    "组件", "设计", "软件", "项目", "优化", "运维",
}
_SKILL_MODIFIERS_SORTED = sorted(_SKILL_MODIFIERS, key=len, reverse=True)


def normalize_skill(raw: str) -> str:
    """归一化技能名称：查别名（大小写不敏感）→ 白名单词大小写统一 → 原样返回。

    别名键统一小写，输入可能是任意大小写（如黄金集标注 "Spring"），
    因此先精确查找，再按小写查找。白名单词的大小写变体（如 "GO"→"Go"、
    "matlab"→"MATLAB"、"Echarts"→"ECharts"）统一到白名单标准写法，
    避免同一技能因大小写不同建出多个图谱节点。

    别名/白名单未命中时，剥离尾随修饰词后重查（"mybatis-plus框架"→"MyBatis"、
    "MySQL 优化"→"MySQL"），命中才返回标准名；仍不命中则原样返回，不武断降级。
    """
    raw = raw.strip()
    if raw in SKILL_ALIAS:
        return SKILL_ALIAS[raw]
    alias = SKILL_ALIAS.get(raw.lower())
    if alias is not None:
        return alias
    canonical = _SKILL_WHITELIST_LOWER.get(raw.lower())
    if canonical is not None:
        return canonical
    for mod in _SKILL_MODIFIERS_SORTED:
        if len(raw) > len(mod) and raw.endswith(mod):
            stripped = raw[: -len(mod)].strip()
            if stripped:
                alias = SKILL_ALIAS.get(stripped.lower())
                if alias is not None:
                    return alias
                canonical = _SKILL_WHITELIST_LOWER.get(stripped.lower())
                if canonical is not None:
                    return canonical
            break  # 只剥一次，无论是否命中
    return raw


def normalize_tool_name(raw: str) -> str:
    """工具名归一化：工具别名（大小写不敏感）→ 白名单/技能别名统一 → 原样返回。

    图谱 Tool 节点按 name 建节点（kg_service._import_jd_tx），工具名不归一化会
    因大小写/拼写变体分裂（Ansys/ANSYS、DeepSeek/Deepseek）。别名键统一小写，
    输入任意大小写；白名单工具词（GitHub、Node.js）复用 _SKILL_WHITELIST_LOWER
    与 SKILL_ALIAS 的统一写法，与技能归一化口径一致。
    """
    raw = raw.strip()
    if not raw:
        return raw
    alias = TOOL_ALIAS.get(raw.lower())
    if alias is not None:
        return alias
    canonical = _SKILL_WHITELIST_LOWER.get(raw.lower())
    if canonical is not None:
        return canonical
    skill_alias = SKILL_ALIAS.get(raw.lower())
    if skill_alias is not None:
        return skill_alias
    return raw


# 白名单外技能信号的噪音判定（观察池过滤 / 白名单扩充候选共用）。
# 词表与判定规则原属 scripts/expand_skill_whitelist.py，提升到词典层供两处复用，
# 保证观察池信号与白名单扩充对"什么不是技能"口径一致。
_POSITION_NOISE = re.compile(
    r"(工程师|技术员|开发工程师|开发$|专员|经理|主管|负责人|设计师|"
    r"架构师|分析师|科学家|研究员|顾问|专家|实习生|助理|店长)$"
)
_EXPERIENCE_NOISE = re.compile(r"(经验|开发|部署|使用|掌握|熟悉|了解|能力|经验$|方向)")
_GENERIC_NOISE = {
    "前端", "后端", "测试", "运维", "算法", "数据库", "大数据", "云",
    "安全", "网络", "搜索", "配置", "操作", "脚本", "报表", "开源",
    "移动", "桌面", "嵌入式", "数据", "框架", "平台", "系统", "项目",
    "团队", "业务", "产品", "架构", "开发", "技术", "管理", "设计",
    "工具", "接口", "协议", "引擎", "服务", "组件", "方案", "功能",
    "经验", "工作", "能力", "要求", "方向", "语言",
}
_COMPOUND_NOISE = re.compile(r"[\/()（）]|天/|月/|年/")
# 别名映射的落点（标准名）是真实技能，永不判噪音
_ALIAS_STANDARDS: set[str] = set(SKILL_ALIAS.values())


def is_noise_skill(name: str) -> bool:
    """启发式噪音判定：非技能标签 / 泛词 / 岗位名与经验描述碎片。

    用于观察池 JD 信号过滤（LLM 误抽"算法工程师""熟悉Redis"等非技能词）与
    白名单扩充候选挖掘。白名单词与别名标准名整体保护（如"嵌入式开发"不以
    "开发"后缀退化判噪），其余按泛词/岗位名/经验碎片规则判定。
    """
    # SKILL_STOPWORDS 优先于白名单保护：显式停用词（含白名单基础词）一律判噪
    # （08-14 盲审迭代：消息队列/数据结构 等上位泛词是 gold 口径不收的，
    # LLM 高频误抽；路由依赖词如计算机视觉/图像处理/统计学保留白名单保护，
    # 防破坏 _POSITION_SKILL_ROUTING 岗位聚合）
    if name in SKILL_STOPWORDS:
        return True
    if name in SKILL_WHITELIST or name in _ALIAS_STANDARDS:
        return False
    if name in _GENERIC_NOISE:
        return True
    if len(name) < 2 or name.isdigit():
        return True
    if _COMPOUND_NOISE.search(name):
        return True
    if _POSITION_NOISE.search(name):
        return True
    if _EXPERIENCE_NOISE.search(name):
        return True
    return False


# 白名单词小写 → 标准写法映射（normalize_skill 用于大小写统一）
_SKILL_WHITELIST_LOWER: dict[str, str] = {w.lower(): w for w in SKILL_WHITELIST}


# 熟练度映射（JD 自然语言 → level 三档，按优先级从高到低匹配）：
# 高级词先匹配（"精通/深入/专家/资深"），避免被中级/初级子串误吞（如"熟练掌握"）
_PROFICIENCY_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("精通", "深入", "专家", "资深"), "高级"),
    (("掌握", "熟练", "独立"), "中级"),
    (("熟悉", "了解", "入门", "基础"), "初级"),
]


def normalize_proficiency(text: str) -> str | None:
    """JD 自然语言熟练度 → level 三档（了解/熟悉→初级、掌握→中级、精通→高级）。

    直接命中规范枚举（初级/中级/高级）原样返回；未命中返回 None（不武断判定）。
    """
    if not text:
        return None
    t = text.strip()
    if t in ("初级", "中级", "高级"):
        return t
    for keywords, level in _PROFICIENCY_KEYWORDS:
        if any(k in t for k in keywords):
            return level
    return None
