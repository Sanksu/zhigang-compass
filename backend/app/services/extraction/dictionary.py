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
    "Ruby", "PHP", "Swift", "Kotlin", "Scala", "R", "MATLAB", "Shell",
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
    "算法设计": "算法",
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
    # 后缀类兜底族：统一低频同类岗，防止剥后缀后产生"财务/业务/研究"等碎片。
    # 已有细分族（数据分析等）位于其前，优先命中不受影响。
    # "分析师"已拆为细分族（_ANALYST_SUB_FAMILIES），不再走统一兜底族
    (("科学家",), "科学家"),
    (("研究员",), "研究员"),
    (("专家",), "专家"),
    (("顾问",), "顾问"),
]


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
}

# 无信息量泛岗位词：归一化结果命中时视为空岗位（不入图）。
# 后 4 行为评估报告 P0-1 新增：真实岗位被剥后缀后残留的无信息量核心词
# （真实族由 _POSITION_KEYWORDS 前置拦截，不会命中这些词）。
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
    "GRC 自动化", "云系统管理员",
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
    "AI/ML Applied Engineer",
    "AI 业务自动化", "机电一体化", "密码应用", "AR/VR 设计验证",
    "Gemini App 合作伙伴", "交通规划", "智能驾驶路测",
    # 问题 1 修复：搜索短词改为复合词限定后，运营/SEO 类岗位不再被算法族吸走，
    # 加停用词使其不入图（P0-A 碎片岗位同类处理）
    "搜索运营", "搜索引擎优化",
}

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
    # 剥后缀后残留核心词再校验一次（评估报告 P0-1）：防止"产品经理"剥成
    # "产品"、"董事总经理"剥成"董事总"等碎片直接入图
    result = core or name
    return "" if result in _POSITION_STOPWORDS else result


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


def normalize_position_name(name: str) -> str:
    """岗位名归一化：英文翻译中文 + 合并同义重复岗位，保留技术栈细分维度。

    步骤：
    1. 英文岗位名翻译为中文（"Software Engineer" → "软件工程师"），与中文岗位合并去重
    2. 整体处理（不再按分隔符取第一段）：混合标题如 "后/前端开发、测试" 会在
       整串中命中关键词族（"前端"）归入主族，避免产生 "后" 这类碎片岗位
    3. 提取技术栈限定词（括号内 "前端开发工程师(React)" 或前缀 "React前端开发工程师"），
       React/Vue/小程序/鸿蒙等作为细分岗位保留，web/h5/html 视为通用归并
    4. 基础岗位名归一化（去级别前缀、循环去后缀、关键词族映射）；
       归一化结果为泛词（"技术"/"后台" 等无信息量）时返回空串，不入图
    5. 技能词不入图：归一化结果命中技能白名单时视为"技能被抽成岗位"，
       返回空串（评估报告 P1，防 SQL/C/FPGA/PyTorch 等技术名词污染岗位图）

    示例：
    - "Software Engineer" / "Senior Software Engineer" → "软件工程师"
    - "前端开发" / "web前端开发工程师" → "前端开发工程师"
    - "React前端开发工程师" / "前端开发工程师(React)" → "React前端开发工程师"
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
        if not base or base in _POSITION_STOPWORDS:
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
        if not base or base in _POSITION_STOPWORDS:
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
    if result in _POSITION_STOPWORDS or _SKILL_WHITELIST_LOWER.get(result.lower()):
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
