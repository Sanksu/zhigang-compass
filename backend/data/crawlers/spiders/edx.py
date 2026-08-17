"""edX 爬虫（国际学习路径数据源）。

策略：
- 用 Playwright 渲染搜索页 https://www.edx.org/search?search_key={keyword}
- 解析 SSR HTML 中的课程卡片（2026 版本，edX 改用 Tailwind CSS）
- 大卡片选择器：a 内含 div[class*="shadow-product-card"]
- 国际源，需走代理
- 产出 CourseItem，用于构建 (Skill)-[:LEARNABLE_VIA]->(Course) 关系

合规：
- 仅采集公开搜索页元数据（标题/院校/类型/时长/级别）
- 每周全量同步，请求间隔 10-20s
- 遵循 edX robots.txt

运行：
  scrapy crawl edx -a keywords=Python,Data-Science -o output/edx.jsonl
  # 国际源，需代理
  $env:HTTPS_PROXY="http://127.0.0.1:7890"
"""

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urljoin

from scrapy import Request, Spider
from scrapy.http import Response

from crawlers.base_spider import make_playwright_request
from crawlers.items import CourseItem
from crawlers.settings import RATE_LIMIT


EDX_BASE = "https://www.edx.org"
# edX 搜索参数是 q（search_key 为旧版参数，已失效，返回浏览模式而非搜索结果）
EDX_SEARCH_URL = "https://www.edx.org/search?q={keyword}"

# 默认搜索关键词：空 = 全量课程浏览（08-16 用户决策，不再内置定向词）
DEFAULT_KEYWORDS: list[str] = []

# edX 大卡片内 a 标签特征类名（用于卡片定位）
EDX_CARD_LINK_XPATH = (
    '//a[contains(@class, "no-underline") and .//div[contains(@class, "shadow-product-card")]]'
)


class EdxSpider(Spider):
    """edX 采集。

    不继承 BaseSpider（非岗位数据），但复用 keywords 参数风格。
    """

    name = "edx"
    platform = "edx"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # -a keywords=Python,Data Science 覆盖默认关键词
        kws = kwargs.get("keywords")
        self.keywords = kws.split(",") if kws else DEFAULT_KEYWORDS
        # -a max_pages=3 控制单关键词翻页数
        self.max_pages = int(kwargs.get("max_pages", "3"))
        # 请求间隔
        limit = RATE_LIMIT.get(self.platform, {})
        delay_range = limit.get("delay_range", (10, 20))
        self.download_delay = sum(delay_range) / 2

    async def start(self):
        """Scrapy 2.13+ 入口：桥接到 start_requests。"""
        for request in self.start_requests():
            yield request

    def start_requests(self):
        # 空关键词 = 全量课程浏览（08-16 用户决策，不再内置定向词）
        keywords = self.keywords or [""]
        for keyword in keywords:
            url = EDX_SEARCH_URL.format(keyword=quote(keyword)) if keyword else "https://www.edx.org/search"
            self.logger.info(f"开始采集 edX: 关键词={keyword or '(全部)'}")
            yield make_playwright_request(
                url,
                meta={"keyword": keyword},
                selector='div[class*="shadow-product-card"], .no-results, [class*="no-results"]',
                wait_timeout=30000,
                scroll_times=max(1, self.max_pages),
                scroll_wait_ms=3500,
            )

    def parse(self, response: Response):
        """解析搜索结果页，产出 CourseItem。

        edX 2026 版 DOM 结构（Tailwind CSS）：
        <a href="/certificates/...|/courses/...|/learn/..." class="no-underline ...">
          <div class="... shadow-product-card ...">
            <div><span class="font-bold text-primary-500">Professional Certificate</span></div>
            <img alt="课程标题" />
            <img alt="机构名" />
            <span>课程标题</span>  (重复)
            <span>机构名</span>     (重复)
            <span>2 courses</span>
            <span>6 months to complete</span>
            <span>Intermediate level</span>
          </div>
        </a>
        """
        # 大卡片：a 标签内含 div[class*="shadow-product-card"]
        cards = response.xpath(EDX_CARD_LINK_XPATH)

        if not cards:
            # 兜底：找含 shadow-product-card 的 div 的父节点（parsel 不支持 ::parent，用 XPath）
            cards = response.xpath('//div[contains(@class, "shadow-product-card")]/..')
            cards = [c for c in cards if c.root.tag == 'a'] if cards else []

        if not cards:
            self.logger.warning(
                f"未解析到课程卡片（keyword={response.meta['keyword']}），"
                f"页面标题: {response.css('title::text').get(default='')}"
            )
            # 检查是否被 Cloudflare 拦截
            if "cloudflare" in response.text.lower() or "challenge" in response.text.lower():
                self.logger.error("被 Cloudflare 拦截，请检查代理配置")
            self.logger.debug(f"页面前 1000 字符: {response.text[:1000]}")
            return

        item_count = 0
        for card in cards:
            item = self._card_to_item(card, response.meta)
            if item:
                item_count += 1
                yield item

        self.logger.info(f"[edx] kw={response.meta['keyword']} 产出 {item_count} 条")

        # 无 URL 翻页：edX 搜索为 SPA 无限滚动，&page=N 会返回空结果，
        # 已通过 _make_playwright_request 在页面内滚动触发加载更多

    def _card_to_item(self, card, meta: dict) -> CourseItem:
        """将单个大卡片转为 CourseItem。"""
        # href
        href = card.xpath('./@href').get()
        if not href:
            return None

        # source_id：从 URL 最后一段提取
        # /certificates/professional-certificate/harvardx-cs50 -> harvardx-cs50
        # /learn/python/harvard-university-cs50 -> harvard-university-cs50
        # /courses/course-v1:edX+DemoX -> course-v1:edX+DemoX
        source_id = href.rstrip("/").split("/")[-1].split("?")[0]
        source_url = urljoin(EDX_BASE, href)

        # 类型徽章：<span class="font-bold text-primary-500 ...">Professional Certificate</span>
        category = card.xpath(
            './/span[contains(@class, "text-primary-500")]/text()'
        ).get(default="").strip()

        # 标题：第一个 <img alt="...">（非机构 logo）
        # 机构 logo 的 alt 通常含 "logo" 或在第二个 img
        img_alts = card.xpath('.//img/@alt').getall()
        title = ""
        institution = ""
        for alt in img_alts:
            alt = alt.strip()
            if not alt:
                continue
            # 跳过 logo（alt 含 "logo"）
            if "logo" in alt.lower():
                continue
            if not title:
                title = alt
            elif not institution:
                institution = alt

        # 兜底：从 spans 提取
        if not title or not institution:
            spans_text = card.xpath('.//span/text()').getall()
            spans_text = [s.strip() for s in spans_text if s.strip()]
            # spans 顺序：[类型, 标题, 机构, 课程数, 时长, 级别]
            # 已知类型，过滤后剩下的就是元数据
            type_set = {"Professional Certificate", "Course", "MicroMasters",
                        "XSeries", "Executive Education", "Graduate Program",
                        "Boot Camp", "Degree", "High School"}
            metadata_spans = [s for s in spans_text if s not in type_set and s != category]
            if not title and metadata_spans:
                title = metadata_spans[0]
                metadata_spans = metadata_spans[1:]
            if not institution and metadata_spans:
                institution = metadata_spans[0]
                metadata_spans = metadata_spans[1:]
        else:
            # 已有 title 和 institution，提取其余 spans 作为元数据
            spans_text = card.xpath('.//span/text()').getall()
            spans_text = [s.strip() for s in spans_text if s.strip()]
            type_set = {"Professional Certificate", "Course", "MicroMasters",
                        "XSeries", "Executive Education", "Graduate Program",
                        "Boot Camp", "Degree", "High School"}
            metadata_spans = [s for s in spans_text
                              if s not in type_set and s != category
                              and s != title and s != institution]

        # 解析元数据 spans（如 "2 courses"、"6 months to complete"、"Intermediate level"）
        # 优先级：week/month/year > courses 数（多课程项目可能两种都有）
        duration = ""
        level = ""
        course_count = ""
        for text in metadata_spans:
            text_lower = text.lower()
            if "level" in text_lower and not level:
                level = text
            elif any(k in text_lower for k in ("week", "month", "year", "day")) and not duration:
                duration = text
            elif "courses" in text_lower and not course_count:
                course_count = text
        # 若无具体时长，用课程数兜底
        if not duration and course_count:
            duration = course_count

        # 评分：edX 列表页通常无评分
        rating = 0.0

        # 描述：列表页无独立描述
        description = ""

        # 用搜索关键词作为分类补充
        if not category:
            category = meta.get("keyword", "")

        item = CourseItem()
        item["source"] = self.platform
        item["source_id"] = source_id
        item["source_url"] = source_url
        item["crawled_at"] = datetime.now(timezone(timedelta(hours=8))).isoformat()
        item["title"] = title
        item["institution"] = institution
        item["platform"] = "edx"
        item["category"] = category
        item["rating"] = rating
        item["enrollment"] = 0  # edX 列表页通常不显示注册人数
        item["duration"] = duration
        item["skills"] = []  # 列表页通常无技能标签
        item["raw_text"] = card.get()
        item["is_desensitized"] = False
        return item


    def _safe_float(text) -> float:
        """安全转 float，失败返回 0.0。"""
        if not text:
            return 0.0
        try:
            match = re.search(r"([\d.]+)", str(text))
            return float(match.group(1)) if match else 0.0
        except (ValueError, TypeError):
            return 0.0
