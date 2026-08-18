"""LinkedIn 爬虫（C 级 — 实验性，需走代理）。

策略（2026-07-29 重构）：
- 主链路：调用 JobSpy（speedyapply/JobSpy，2026-02 仍维护）采集
  - JobSpy 内部解析 LinkedIn JSON-LD 结构化数据，字段丰富稳定
  - JobSpy 是同步阻塞库，与 Scrapy Twisted 冲突 → 用 subprocess 调独立脚本
- 参考项目：https://github.com/speedyapply/JobSpy

聚焦治理（08-18）：JobSpy 抓取 LinkedIn 公开页覆盖全行业（Crew Member /
Pharmacist / Department Manager 等非技术基础岗占比高），与系统技术岗定位
不符。产出前按标题技术关键词白名单过滤（_is_tech_title），仅保留技术岗；
关键词覆盖系统 12 类岗位口径（后端/AI/算法/嵌入式/全栈/运维/数据/前端/
数据分析/测试/网络安全/其他技术岗）。每日上限 50 条仍按原始抓取量计，
过滤后产出 ≤ 上限。

⚠️ 合规提醒：
- 仅采集公开搜索页
- 需走 Clash/V2Ray 代理（HTTPS_PROXY 环境变量）
- LinkedIn 公开页在中国大陆 IP 无法访问，必须走代理

运行：
  # 先启动 Clash（7890 端口）
  $env:HTTPS_PROXY="http://127.0.0.1:7890"
  scrapy crawl linkedin_public -a keywords=Python -a cities="New York" -o output/linkedin_public.jsonl
"""

import re

from crawlers.spiders.jobspy_base import JobSpyBaseSpider

# 技术岗标题关键词白名单（08-18 聚焦治理）。
# 中文按系统 12 类岗位口径；英文需词边界匹配（防 "system"→"systematic"、
# "recruiter" 等子串误报），中文无词边界概念保持子串匹配。
_TECH_TITLE_KEYWORDS = (
    # 中文
    "开发", "工程师", "软件", "算法", "人工智能", "机器学习", "深度学习", "大模型",
    "数据", "测试", "运维", "架构", "安全", "网络", "嵌入式", "硬件", "前端", "后端",
    "全栈", "产品", "设计", "自动化", "云计算", "云原生", "区块链", "量化", "数据库",
    "技术支持", "实施", "数据分析", "研发",
    # 英文（词边界匹配）
    "software", "engineer", "developer", "frontend", "backend", "fullstack",
    "full-stack", "data", "analyst", "scientist", "machine learning",
    "artificial intelligence", "ai", "ml", "devops", "sre", "site reliability",
    "security", "cyber", "network", "embedded", "android", "ios", "java",
    "python", "golang", "rust", "c++", "react", "vue", "node", "cloud",
    "architect", "tester", "qa", "automation", "product manager", "ux", "ui",
    "mobile", "database", "dba", "it support", "infrastructure",
)

_ASCII_ONLY = re.compile(r"^[\x00-\x7f]+$")


def _is_tech_title(title: str) -> bool:
    """标题是否命中技术关键词白名单。

    英文关键词词边界匹配（大小写不敏感，防子串误报）；
    中文关键词子串匹配；空白标题视为非技术岗（宁可丢弃不误收）。
    """
    lowered = (title or "").strip().lower()
    if not lowered:
        return False
    for keyword in _TECH_TITLE_KEYWORDS:
        if _ASCII_ONLY.match(keyword):
            if re.search(
                rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", lowered
            ):
                return True
        elif keyword in lowered:
            return True
    return False


class LinkedInPublicSpider(JobSpyBaseSpider):
    name = "linkedin_public"
    platform = "linkedin_public"
    site_name = "linkedin"

    # LinkedIn 默认搜索美国城市
    cities = ["New York", "San Francisco", "Seattle", "Boston", "Remote"]

    # 单次采集岗位数上限
    results_wanted = 100

    def start_requests(self):
        """产出前按技术关键词白名单过滤（08-18 聚焦治理）。"""
        for item in super().start_requests():
            if not _is_tech_title(item.get("title", "")):
                self.logger.debug(
                    f"[{self.platform}] 非技术岗标题过滤: {(item.get('title') or '')[:60]}"
                )
                continue
            yield item
