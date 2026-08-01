"""Stack Overflow 爬虫（技术热点观察池数据源）。

策略：
- 爬取 https://stackoverflow.com/questions/tagged/{tag}?tab={Newest|Frequent|Bountied} 公开页
- 解析 SSR HTML 中的问题卡片（div.s-post-summary 或旧版 .question-summary）
- 无需 API key，无需 token
- 产出 CommunityTrendItem（trend_type=newest/hot）

合规：
- 仅采集公开问题页元数据（标题/标签/票数/浏览数）
- 请求间隔 6-12s，遵循 robots.txt

运行：
  scrapy crawl stackoverflow -a tags=python,machine-learning,java -a tab=Newest -a max_pages=3 -o output/stackoverflow.jsonl
  # 国际源，需代理
  $env:HTTPS_PROXY="http://127.0.0.1:7890"
"""

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urljoin

from scrapy import Request, Spider
from scrapy.http import Response
from scrapy_playwright.page import PageMethod

from crawlers.items import CommunityTrendItem
from crawlers.settings import RATE_LIMIT


SO_TAG_URL = "https://stackoverflow.com/questions/tagged/{tag}?tab={tab}&page={page}"

# 默认关注的标签（覆盖项目 AI/大数据/全栈方向）
DEFAULT_TAGS = ["python", "machine-learning", "java", "javascript", "sql"]

# 默认 tab：Newest（最新）/ Frequent（热门）/ Bountied（悬赏）
DEFAULT_TAB = "Newest"


class StackoverflowSpider(Spider):
    """Stack Overflow 标签页采集。

    不继承 BaseSpider（非岗位数据），直接继承 Spider。
    """

    name = "stackoverflow"
    platform = "stackoverflow"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # -a tags=python,machine-learning 覆盖默认标签
        tags = kwargs.get("tags")
        self.tags = tags.split(",") if tags else DEFAULT_TAGS
        # -a tab=Newest|Frequent|Bountied
        self.tab = kwargs.get("tab", DEFAULT_TAB)
        # -a max_pages=3 控制单标签翻页数
        self.max_pages = int(kwargs.get("max_pages", "3"))
        # 请求间隔
        limit = RATE_LIMIT.get(self.platform, {})
        delay_range = limit.get("delay_range", (6, 12))
        self.download_delay = sum(delay_range) / 2

    async def start(self):
        """Scrapy 2.13+ 入口：桥接到 start_requests。"""
        for request in self.start_requests():
            yield request

    def start_requests(self):
        for tag in self.tags:
            self.logger.info(f"开始采集 Stack Overflow 标签: {tag} (tab={self.tab})")
            url = SO_TAG_URL.format(tag=quote(tag), tab=self.tab, page=1)
            yield self._make_playwright_request(
                url,
                meta={"tag": tag, "page": 1},
            )

    def parse(self, response: Response):
        """解析 Stack Overflow 标签页，产出 CommunityTrendItem。"""
        # 新版 SO 用 div.s-post-summary，旧版用 .question-summary
        cards = response.css("div.s-post-summary, div.question-summary")

        if not cards:
            self.logger.warning(
                f"未解析到问题卡片（tag={response.meta['tag']} page={response.meta['page']}），"
                f"页面标题: {response.css('title::text').get(default='')}"
            )
            # 检查是否被 Cloudflare 拦截
            if "cloudflare" in response.text.lower() or "cf-browser-verification" in response.text.lower():
                self.logger.error("被 Cloudflare 拦截，请检查代理配置")
            self.logger.debug(f"页面前 1000 字符: {response.text[:1000]}")
            return

        for card in cards:
            item = self._card_to_item(card, response.meta)
            if item:
                yield item

        # 翻页
        current_page = response.meta.get("page", 1)
        if current_page < self.max_pages:
            tag = response.meta["tag"]
            next_url = SO_TAG_URL.format(
                tag=quote(tag), tab=self.tab, page=current_page + 1
            )
            yield self._make_playwright_request(
                next_url,
                meta={"tag": tag, "page": current_page + 1},
            )

    def _make_playwright_request(self, url: str, meta: dict, callback=None):
        """构造 Playwright 渲染请求：用真实浏览器绕过 Cloudflare TLS 指纹检测。"""
        return Request(
            url,
            callback=callback or self.parse,
            meta={
                "playwright": True,
                "playwright_page_methods": [
                    # 等待问题卡片或错误页出现
                    PageMethod(
                        "wait_for_selector",
                        "div.s-post-summary, div.question-summary, .js-error-message, #mainbar",
                        timeout=20000,
                    ),
                ],
                **meta,
            },
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            },
            dont_filter=True,
        )

    def _card_to_item(self, card, meta: dict) -> CommunityTrendItem:
        """将单个问题卡片转为 CommunityTrendItem。"""
        # 标题与链接：h3.s-post-summary--content-title > a（新版）/ h3 > a（旧版）
        title_link = card.css("h3.s-post-summary--content-title a, h3 a")
        title = title_link.css("::text").get(default="").strip()
        href = title_link.css("::attr(href)").get()

        if not title or not href:
            return None

        # 问题 ID 从 href 提取（/questions/12345/title）
        question_id = ""
        match = re.search(r"/questions/(\d+)", href)
        if match:
            question_id = match.group(1)

        url = urljoin("https://stackoverflow.com", href)

        # 统计指标：votes / answers / views
        # 用 itemprop 直接定位更稳（避免被 Deleted/Featured 等额外标签错位）
        votes_text = card.css('[itemprop="upvoteCount"]::text').get()
        votes = self._safe_int(votes_text)
        answers_text = card.css('[itemprop="answerCount"]::text').get()
        answers = self._safe_int(answers_text)
        # views 无 itemprop，通过 unit 文本定位（"views" 单元对应的 number）
        # Scrapy CSS 不支持 :last-child，改用 xpath 找含 "views" 文本的 stats-item
        views_text = card.xpath(
            './/div[contains(@class, "s-post-summary--stats-item")]'
            '//*[contains(@class, "s-post-summary--stats-item-unit") and contains(text(), "views")]'
            '/preceding-sibling::*[contains(@class, "s-post-summary--stats-item-number")][1]/text()'
        ).get()
        views = self._parse_views(views_text)

        # 标签
        tags = [t.strip() for t in card.css("a.post-tag::text").getall() if t.strip()]

        # 提问时间：span.relativetime 的 title 属性（如 "2026-07-29 10:30:00Z"）
        asked_at = card.css("span.relativetime::attr(title)").get(default="")
        if not asked_at:
            asked_at = card.css("span.relativetime::text").get(default="").strip()

        item = CommunityTrendItem()
        item["source"] = self.platform
        item["source_id"] = question_id
        item["source_url"] = url
        item["crawled_at"] = datetime.now(timezone(timedelta(hours=8))).isoformat()
        item["title"] = title
        item["description"] = ""  # SO 列表页无摘要
        item["url"] = url
        item["tags"] = tags
        item["votes"] = votes
        item["views"] = views
        item["answers"] = answers
        item["asked_at"] = asked_at
        item["language"] = meta.get("tag", "")  # 用 tag 作为 language 字段复用
        item["trend_type"] = "newest" if self.tab.lower() == "newest" else "hot"
        item["raw_text"] = card.get()
        item["is_desensitized"] = False
        return item

    @staticmethod
    def _parse_views(text: str) -> int:
        """解析浏览数（如 '1.2k' → 1200, '3m' → 3000000）。"""
        if not text:
            return 0
        text = text.strip().lower().replace(",", "")
        try:
            if "k" in text:
                return int(float(text.replace("k", "")) * 1000)
            if "m" in text:
                return int(float(text.replace("m", "")) * 1000000)
            return int(text)
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _safe_int(text) -> int:
        """安全转 int，失败返回 0。"""
        if not text:
            return 0
        try:
            return int(str(text).strip().replace(",", ""))
        except (ValueError, TypeError):
            return 0
