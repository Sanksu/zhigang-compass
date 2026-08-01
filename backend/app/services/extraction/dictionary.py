"""技能别名词典与白名单。

用于 LLM 抽取后的词典后过滤（设计文档 5.2 节）：
1. SKILL_ALIAS — 将别名/口语化表述映射为标准技能名
2. SKILL_WHITELIST — 标准技能白名单（幻觉防控第三道防线）
"""

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
}

# 标准技能白名单（第三道防线，未命中走审核）
SKILL_WHITELIST: set[str] = {
    # 编程语言
    "Python", "Java", "JavaScript", "TypeScript", "Go", "Rust", "C++", "C#",
    "Ruby", "PHP", "Swift", "Kotlin", "Scala", "R", "MATLAB", "Shell",
    # 前端
    "React", "Vue.js", "Angular", "HTML", "HTML5", "CSS", "Webpack", "Vite",
    "Tailwind CSS", "Next.js", "Nuxt.js", "Bootstrap", "ECharts", "Three.js",
    "数据可视化", "UI设计", "前端工程化",
    # 后端
    "Spring Boot", "Spring Cloud", "Django", "Flask", "FastAPI", "Express.js",
    "Node.js", "ASP.NET", "Microservices", "MyBatis", "Scrapy",
    # 数据库
    "MySQL", "PostgreSQL", "MongoDB", "Redis", "Elasticsearch", "SQLite",
    "Oracle", "SQL Server", "Cassandra", "ClickHouse",
    # 大数据
    "Hadoop", "Apache Spark", "Apache Flink", "Apache Kafka", "Hive", "HBase",
    "Airflow", "PySpark", "数据治理",
    # 云原生/DevOps
    "Docker", "Kubernetes", "Jenkins", "Git", "GitHub Actions", "Terraform",
    "Ansible", "Prometheus", "Grafana", "CI/CD", "Nginx", "系统运维",
    # AI/ML
    "PyTorch", "TensorFlow", "scikit-learn", "大语言模型", "检索增强生成",
    "机器学习", "深度学习", "自然语言处理", "计算机视觉", "数据分析",
    "OpenAI API", "LangChain", "LlamaIndex",
    "大模型算法", "推荐算法", "图像算法", "风控算法", "强化学习", "SLAM算法",
    "广告算法", "AIGC", "多模态模型", "语音识别", "运筹优化算法", "具身智能",
    "机器人", "OpenCV", "嵌入式开发", "自动化测试", "多线程", "三维开发",
    "GIS开发", "大模型评测", "音频标注", "视频标注",
    # 通用软技能
    "团队协作", "沟通能力", "项目管理", "需求分析", "产品设计",
}


def normalize_skill(raw: str) -> str:
    """归一化技能名称：查别名（大小写不敏感），去除首尾空白。

    别名键统一小写，输入可能是任意大小写（如黄金集标注 "Spring"），
    因此先精确查找，再按小写查找。
    """
    raw = raw.strip()
    if raw in SKILL_ALIAS:
        return SKILL_ALIAS[raw]
    return SKILL_ALIAS.get(raw.lower(), raw)
