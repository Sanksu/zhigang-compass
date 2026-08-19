"""edX 爬虫（国际学习路径数据源）。

数据面（2026-08-19 重写）：edX 改版后搜索页为全客户端渲染（无课程卡片、
无 __NEXT_DATA__、headless 无法复现结果），旧 Playwright 卡片选择器失效。
改走稳定公开数据面：

  1. 拉取 ``https://www.edx.org/sitemap.xml``（sitemap 主动公开，robots.txt
     允许英文 ``/learn/*``，仅禁 ``/es/learn/*`` 等）→ 提取课程详情 URL
     ``/learn/{subject}/{slug}``（约 5300 条）
  2. 详情页 SSR 完整返回（无需 JS 渲染），解析 ``<script type="application/ld+json">``
     中 ``@type=Course`` 节点——标题/机构/时长/级别/技能等全字段结构化

合规：
  - 仅采集 sitemap 公开课程页元数据（标题/院校/时长/级别/技能）
  - 每日同步，请求间隔 10-20s
  - 不爬 robots Disallow 路径（/es/learn/*、/preview/ 等）

运行：
  scrapy crawl edx -o output/edx.jsonl                    # 全量（默认每批 100）
  scrapy crawl edx -a keywords=Python -o output/edx.jsonl  # 关键词过滤
  scrapy crawl edx -a limit=5 -o output/edx.jsonl          # 限制批内数量（调试）
"""

import json
import re
from datetime import datetime, timedelta, timezone

from scrapy import Request, Spider
from scrapy.http import Response

from crawlers.items import CourseItem
from crawlers.settings import RATE_LIMIT


EDX_BASE = "https://www.edx.org"
EDX_SITEMAP_URL = "https://www.edx.org/sitemap.xml"
# 课程详情页 URL 形态：/learn/{subject}/{slug}
_COURSE_RE = re.compile(r"^https://www\.edx\.org/learn/[^/]+/[^/]+$")

# 简单真实 UA：edX 对无 UA/默认 UA 的 SSR 请求可能返回错误页（bot 检测）
_EDX_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 详情页 JSON-LD 脚本提取
_LD_RE = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)


class EdxSpider(Spider):
    """edX 采集：sitemap 课程索引 → 详情页 JSON-LD 解析。

    不继承 BaseSpider（非岗位数据），但复用 keywords 参数风格。
    """

    name = "edx"
    platform = "edx"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # -a keywords=Python,Data Science 过滤 sitemap（URL 含任一关键词）
        kws = kwargs.get("keywords")
        self.keywords = [k.lower() for k in kws.split(",") if k.strip()] if kws else []
        # 每批采集上限（默认 100，对应每日同步节奏；调试用 -a limit=N）
        self.limit = int(kwargs.get("limit") or kwargs.get("max_results") or 100)
        # 请求间隔
        limit_cfg = RATE_LIMIT.get(self.platform, {})
        delay_range = limit_cfg.get("delay_range", (10, 20))
        self.download_delay = sum(delay_range) / 2

    async def start(self):
        """Scrapy 2.13+ 入口：桥接到 start_requests。"""
        for request in self.start_requests():
            yield request

    def start_requests(self):
        """入口：请求 sitemap.xml 获取课程 URL 索引。"""
        self.logger.info(f"开始采集 edX sitemap（limit={self.limit}, "
                         f"keywords={self.keywords or '(全部)'}）")
        yield Request(
            EDX_SITEMAP_URL,
            callback=self.parse_sitemap,
            headers={"User-Agent": _EDX_UA},
            dont_filter=True,
        )

    def parse_sitemap(self, response: Response):
        """解析 sitemap，筛选课程详情 URL 并逐个请求详情页。"""
        locs = response.xpath("//*[local-name()='loc']/text()").getall()
        courses = [
            u for u in locs
            if _COURSE_RE.match(u)
            and "/es/" not in u and "/aprende/" not in u
        ]
        if self.keywords:
            courses = [u for u in courses if any(k in u.lower() for k in self.keywords)]
        courses = courses[: self.limit]

        if not courses:
            self.logger.warning(
                f"[edx] sitemap 未筛出课程 URL（keywords={self.keywords or '(全部)'}），"
                f"sitemap loc 总数={len(locs)}"
            )
            return

        self.logger.info(f"[edx] sitemap 筛出 {len(courses)} 个课程 URL，开始逐个抓详情页")
        for url in courses:
            yield Request(url, callback=self.parse_course, headers={"User-Agent": _EDX_UA})

    def parse_course(self, response: Response):
        """解析课程详情页的 JSON-LD Course 节点，产出 CourseItem。"""
        item = self._item_from_jsonld(response)
        if item:
            yield item
        else:
            self.logger.warning(
                f"[edx] 详情页无 Course JSON-LD: {response.url} "
                f"(title={response.css('title::text').get(default='')[:60]})"
            )

    def _item_from_jsonld(self, response: Response) -> CourseItem | None:
        """从详情页 JSON-LD 中取 @type=Course 节点，映射为 CourseItem。"""
        course = None
        for block in _LD_RE.findall(response.text):
            try:
                data = json.loads(block)
            except (ValueError, TypeError):
                continue
            graph = data.get("@graph") if isinstance(data, dict) else None
            nodes = graph if isinstance(graph, list) else [data]
            for node in nodes:
                if isinstance(node, dict) and node.get("@type") == "Course":
                    course = node
                    break
            if course:
                break
        if not course:
            return None

        name = str(course.get("name") or "").strip()
        if not name:
            return None

        institution = ""
        provider = course.get("provider")
        if isinstance(provider, list):
            for p in provider:
                if isinstance(p, dict) and p.get("name"):
                    institution = str(p["name"])
                    break
        elif isinstance(provider, dict) and provider.get("name"):
            institution = str(provider["name"])

        # category：取 URL 的 subject 段（/learn/{subject}/{slug}）
        m = re.match(r"https://www\.edx\.org/learn/([^/]+)/", response.url)
        category = m.group(1) if m else ""

        # rating / enrollment（缺省留 0）
        rating = 0.0
        agg = course.get("aggregateRating")
        if isinstance(agg, dict):
            try:
                rating = float(str(agg.get("ratingValue") or 0))
            except (TypeError, ValueError):
                rating = 0.0
        enrollment = 0
        if isinstance(course.get("totalHistoricalEnrollment"), (int, float)):
            enrollment = int(course.get("totalHistoricalEnrollment"))

        duration = self._iso8601_duration(course.get("timeRequired"))

        # start_date：第一个 hasCourseInstance.startDate（可为空）
        start_date = ""
        instances = course.get("hasCourseInstance")
        if isinstance(instances, list):
            for inst in instances:
                if isinstance(inst, dict) and inst.get("startDate"):
                    start_date = str(inst["startDate"])
                    break

        # skills：about 列表里 Lightcast 技能标签（LEARNABLE_VIA 关键）
        skills = []
        about = course.get("about")
        if isinstance(about, list):
            for a in about:
                if isinstance(a, dict) and a.get("name"):
                    skills.append(str(a["name"]))

        source_id = str(course.get("courseCode") or "").strip() or response.url.rstrip("/").split("/")[-1]

        item = CourseItem()
        item["source"] = self.platform
        item["source_id"] = source_id
        item["source_url"] = response.url
        item["crawled_at"] = datetime.now(timezone(timedelta(hours=8))).isoformat()
        item["title"] = name
        item["institution"] = institution
        item["platform"] = "edx"
        item["category"] = category
        item["rating"] = rating
        item["enrollment"] = enrollment
        item["duration"] = duration
        item["description"] = str(course.get("description") or "")
        item["start_date"] = start_date
        item["skills"] = skills
        item["raw_text"] = json.dumps(course, ensure_ascii=False)
        item["is_desensitized"] = False
        return item

    @staticmethod
    def _iso8601_duration(value) -> str:
        """ISO8601 时长（P4W / P3M / PT6H）转人类可读文本，失败返回空串。"""
        if not value:
            return ""
        s = str(value).strip()
        m = re.match(r"P(?:(\d+)W|(\d+)M|T(?:(\d+)H)?)?", s)
        if not m:
            return ""
        months, weeks, hours = m.group(2), m.group(1), m.group(3)
        if months:
            return f"{months} months to complete"
        if weeks:
            return f"{weeks} weeks to complete"
        if hours:
            return f"{hours} hours to complete"
        return ""
