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
    # 框架与库
    "spring": "Spring Boot",
    "springboot": "Spring Boot",
    "vue": "Vue.js",
    "react": "React",
    "node": "Node.js",
    "express": "Express.js",
    # 大数据
    "hadoop": "Hadoop",
    "spark": "Apache Spark",
    "flink": "Apache Flink",
    "kafka": "Apache Kafka",
    # 云原生
    "k8s": "Kubernetes",
    "docker": "Docker",
    # AI/ML
    "pytorch": "PyTorch",
    "tf": "TensorFlow",
    "sklearn": "scikit-learn",
    "大模型": "大语言模型",
    "llm": "大语言模型",
    "rag": "检索增强生成",
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
    "React", "Vue.js", "Angular", "HTML", "CSS", "Webpack", "Vite",
    "Tailwind CSS", "Next.js", "Nuxt.js",
    # 后端
    "Spring Boot", "Spring Cloud", "Django", "Flask", "FastAPI", "Express.js",
    "Node.js", "ASP.NET", "Microservices",
    # 数据库
    "MySQL", "PostgreSQL", "MongoDB", "Redis", "Elasticsearch", "SQLite",
    "Oracle", "SQL Server", "Cassandra", "ClickHouse",
    # 大数据
    "Hadoop", "Apache Spark", "Apache Flink", "Apache Kafka", "Hive", "HBase",
    "Airflow",
    # 云原生/DevOps
    "Docker", "Kubernetes", "Jenkins", "Git", "GitHub Actions", "Terraform",
    "Ansible", "Prometheus", "Grafana", "CI/CD",
    # AI/ML
    "PyTorch", "TensorFlow", "scikit-learn", "大语言模型", "检索增强生成",
    "机器学习", "深度学习", "自然语言处理", "计算机视觉", "数据分析",
    "OpenAI API", "LangChain", "LlamaIndex",
    # 通用软技能
    "团队协作", "沟通能力", "项目管理", "需求分析", "产品设计",
}


def normalize_skill(raw: str) -> str:
    """归一化技能名称：查别名，去除首尾空白。"""
    raw = raw.strip()
    return SKILL_ALIAS.get(raw, raw)
