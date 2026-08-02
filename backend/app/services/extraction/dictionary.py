"""技能别名词典与白名单。

用于 LLM 抽取后的词典后过滤（设计文档 5.2 节）：
1. SKILL_ALIAS — 将别名/口语化表述映射为标准技能名
2. SKILL_WHITELIST — 标准技能白名单（幻觉防控第三道防线）
3. SKILL_STOPWORDS — 行业/业务领域词黑名单（防 LLM 幻觉技能入图）
4. normalize_position_name — 岗位名归一化（合并同义重复岗位）
"""

import re

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


# 行业/业务领域/招聘福利词黑名单：LLM 在正文缺失的 JD 上常将这些词误抽为技能
SKILL_STOPWORDS: set[str] = {
    "保险", "金融", "银行", "证券", "地产", "零售", "电商", "餐饮", "医疗",
    "教育", "旅游", "物流", "汽车", "能源", "制造", "农业", "传媒", "广告",
    "娱乐", "生活服务", "车联网", "无人机组装测试", "五险", "五险一金", "社保",
    "公积金", "双休", "不加班", "福利", "年终奖", "提成", "股票期权",
    "运营", "销售", "客服", "市场", "行政", "财务", "人力资源", "公关",
    "采购", "前台", "助理岗", "兼职", "实习岗",
}

# 岗位名关键词 → 标准岗位名（合并同义重复岗位，设计文档 4.5 实体对齐的轻量实现）
_POSITION_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("架构",), "架构师"),
    (("测试",), "测试工程师"),
    (("运维",), "运维工程师"),
    (("网络",), "网络工程师"),
    (("嵌入式",), "嵌入式开发工程师"),
    (("算法", "推荐", "图像", "视觉", "语音", "NLP", "自然语言", "大模型",
      "深度学习", "机器学习", "AI", "人工智能", "风控"), "算法工程师"),
    (("数据开发", "大数据", "数仓", "ETL"), "大数据开发工程师"),
    (("数据分析", "商业分析", "数据挖掘"), "数据分析师"),
    (("前端", "web", "h5", "html", "css", "ui"), "前端开发工程师"),
    (("后端", "后台", "服务端"), "后端开发工程师"),
    (("java",), "Java开发工程师"),
    (("python",), "Python开发工程师"),
    (("golang", "go"), "Go开发工程师"),
    (("c/c++", "c++", "c语言"), "C++开发工程师"),
    (("全栈",), "全栈工程师"),
    (("游戏",), "游戏开发工程师"),
    (("硬件",), "硬件工程师"),
    (("软件",), "软件开发工程师"),
]

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
    "data scientist": "数据分析师",
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
}

# 无信息量泛岗位词：归一化结果命中时视为空岗位（不入图）
_POSITION_STOPWORDS: set[str] = {"技术", "开发", "工程师", "管理", "专员", "前台", "文员"}

# 岗位名前缀修饰词（级别/招聘形态），归一化时去除
_POSITION_PREFIX_RE = re.compile(r"^(初级|中级|高级|资深|专家|助理|实习|见习|应届|研发|资深)")
# 岗位名后缀（含"开发/技术员/程序员"等变体），归一化时去除后按关键词重映射
_POSITION_SUFFIX_RE = re.compile(
    r"(工程师|技术员|程序员|研发人员|研发|开发|设计师|经理|主管|负责人|专员)$"
)


def _normalize_base(name: str) -> str:
    """基础岗位名归一化：循环去后缀至核心词，再按关键词族映射。"""
    core = name
    while core:
        low = core.lower()
        for keywords, standard in _POSITION_KEYWORDS:
            if any(k.lower() in low for k in keywords):
                return standard
        next_core = _POSITION_SUFFIX_RE.sub("", core).strip()
        if next_core == core or not next_core:
            break
        core = next_core
    return core or name


def _translate_en_position(name: str) -> str | None:
    """英文岗位名 → 中文标准名。

    匹配优先级：精确 → 逗号前主标题 → 最长子串（单词边界，容忍逗号/连字符）。
    """
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

    示例：
    - "Software Engineer" / "Senior Software Engineer" → "软件工程师"
    - "前端开发" / "web前端开发工程师" → "前端开发工程师"
    - "React前端开发工程师" / "前端开发工程师(React)" → "React前端开发工程师"
    - "技术" → ""（泛词不入图）
    """
    translated = _translate_en_position(name)
    if translated:
        # 翻译结果再过中文归一化，确保与中文路径岗位名统一（如"软件工程师"→"软件开发工程师"）
        base = _normalize_base(translated)
        if not base or base in _POSITION_STOPWORDS:
            return ""
        return base

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

    base = _normalize_base(base)
    if not base or base in _POSITION_STOPWORDS:
        return ""
    if tech:
        return tech + base
    if paren_tech:
        for match, display in _TECH_STACKS:
            if paren_tech.lower() == match:
                return display + base
    return base


def normalize_skill(raw: str) -> str:
    """归一化技能名称：查别名（大小写不敏感），去除首尾空白。

    别名键统一小写，输入可能是任意大小写（如黄金集标注 "Spring"），
    因此先精确查找，再按小写查找。
    """
    raw = raw.strip()
    if raw in SKILL_ALIAS:
        return SKILL_ALIAS[raw]
    return SKILL_ALIAS.get(raw.lower(), raw)


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
