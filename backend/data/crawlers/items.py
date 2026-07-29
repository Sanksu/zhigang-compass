"""Scrapy Item 定义：JD / 课程 / 论文 / 社区趋势统一字段。"""

from scrapy import Item, Field


class _BaseItem(Item):
    """所有数据源的共享字段，子类通过继承复用。"""

    # ---- 来源标识 ----
    source = Field()            # boss / icourse163 / coursera / edx / arxiv / github / stackoverflow ...
    source_url = Field()        # 原始 URL
    source_id = Field()         # 平台内唯一 ID
    crawled_at = Field()        # 采集时间 ISO8601

    # ---- 原始数据 ----
    raw_text = Field()          # 原始 HTML/JSON 备份

    # ---- 合规 ----
    is_desensitized = Field()   # 是否已脱敏
    compliance_note = Field()   # 合规备注

    # ---- 内部 ----
    _fingerprint = Field()      # source + source_id 的 SHA256，用于去重


class JobItem(_BaseItem):
    """招聘信息（boss / zhilian / linkedin / monster / lagou / indeed / glassdoor / maimai）。"""

    # ---- 岗位信息 ----
    title = Field()             # 岗位名称
    company = Field()           # 公司名称
    location = Field()          # 工作地点（城市）
    salary = Field()            # 薪资范围文本
    experience = Field()        # 经验要求
    education = Field()         # 学历要求

    # ---- 核心内容 ----
    description = Field()       # 岗位职责（原始文本）
    requirements = Field()      # 任职要求（原始文本）

    # ---- 元数据 ----
    post_date = Field()         # 发布日期
    job_type = Field()          # 全职/兼职/实习
    tags = Field()              # 平台标签列表


class CourseItem(_BaseItem):
    """课程信息（icourse163 / coursera / edx）。

    用于构建 (Skill)-[:LEARNABLE_VIA]->(Course) 关系，Neo4j 节点 ID prefix=co。
    """

    # ---- 课程信息 ----
    title = Field()             # 课程名
    instructor = Field()        # 讲师
    institution = Field()       # 院校/机构
    platform = Field()          # 平台（icourse163 / coursera / edx）
    category = Field()          # 分类（如 "计算机科学"）
    description = Field()       # 课程简介

    # ---- 课程元数据 ----
    rating = Field()            # 评分（0-5）
    enrollment = Field()        # 注册人数
    duration = Field()          # 时长（周/小时文本）
    start_date = Field()        # 开课时间

    # ---- 技能 ----
    skills = Field()            # 技能标签列表


class PaperItem(_BaseItem):
    """学术论文（arxiv）。

    用于「技术热点观察池」，不独立触发 candidate，仅作 candidate→emerging 阶段置信度加分。
    """

    # ---- 论文信息 ----
    title = Field()             # 标题
    authors = Field()           # 作者列表
    abstract = Field()          # 摘要
    categories = Field()        # arXiv 分类（如 cs.AI）
    published = Field()         # 发布日期
    updated = Field()           # 更新日期

    # ---- 元数据 ----
    doi = Field()               # DOI
    pdf_url = Field()           # PDF 链接
    citation_count = Field()    # 引用数（若可获取）


class CommunityTrendItem(_BaseItem):
    """社区趋势信号（github / stackoverflow）。

    用于「技术热点观察池」，不独立触发 candidate。
    GitHub: 仓库趋势（stars/forks/language）；Stack Overflow: 问题热度（votes/views/tags）。
    """

    # ---- 通用内容 ----
    title = Field()             # 仓库名（github）/ 问题标题（stackoverflow）
    description = Field()       # 仓库描述 / 问题摘要
    url = Field()               # 仓库/问题 URL

    # ---- GitHub 趋势指标 ----
    stars = Field()             # 总 star 数
    forks = Field()             # 总 fork 数
    stars_today = Field()       # 今日新增 star
    language = Field()          # 主语言

    # ---- Stack Overflow 指标 ----
    tags = Field()              # 标签列表
    votes = Field()             # 票数
    views = Field()             # 浏览数
    answers = Field()           # 回答数
    asked_at = Field()          # 提问时间

    # ---- 趋势类型 ----
    trend_type = Field()        # trending / hot / newest
