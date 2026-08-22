"""Coursera 爬虫（国际学习路径数据源）。

策略：
- 用 Playwright 渲染搜索页 https://www.coursera.org/search?query={keyword}
- 解析 SSR HTML 中的课程卡片（li.cds-grid-item 或 a.cds-CommonCard-titleLink）
- 国际源，需走代理
- 产出 CourseItem，用于构建 (Skill)-[:LEARNABLE_VIA]->(Course) 关系

合规：
- 仅采集公开搜索页元数据（标题/讲师/院校/评分/注册数）
- 每周全量同步，请求间隔 10-20s
- 遵循 Coursera robots.txt

运行：
  scrapy crawl coursera -a keywords=Python,Machine-Learning -o output/coursera.jsonl
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


COURSERA_BASE = "https://www.coursera.org"
# 08-14 合规修复：coursera robots.txt 明确 Disallow /search（搜索页禁止爬取），
# /browse 路径未禁（实测 200 + ProductCard 结构一致）——改用 browse 页 + query 过滤
# （仍为公开课程元数据，符合 robots 规则）
# 08-22 实证：/browse 服务端**无视 query 参数**（带/不带 query SSR 返回同一批热门课），
# "query 过滤"只能本地做——定向采集必须按关键词过滤卡片，否则会把无关热门课全量入库。
# 覆盖面局限：browse 目录是热门/目录子集，长尾技能（Airflow/PostgreSQL 等）大概率
# 无命中（宁空勿噪），定向补采以 edx sitemap 关键词过滤为主力。
COURSERA_SEARCH_URL = "https://www.coursera.org/browse?query={keyword}"

# 默认搜索关键词：空 = browse 热门课全量（08-16 用户决策，不再内置定向词）
DEFAULT_KEYWORDS: list[str] = []


class CourseraSpider(Spider):
    """Coursera 采集。

    不继承 BaseSpider（非岗位数据），但复用 keywords 参数风格。
    """

    name = "coursera"
    platform = "coursera"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # -a keywords=Python,Machine Learning 覆盖默认关键词
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
        # 空关键词 = browse 热门课全量（08-16 用户决策）
        keywords = self.keywords or [""]
        for keyword in keywords:
            url = COURSERA_SEARCH_URL.format(keyword=quote(keyword)) if keyword else "https://www.coursera.org/browse"
            self.logger.info(f"开始采集 Coursera: 关键词={keyword or '(热门)'}")
            yield make_playwright_request(
                url,
                meta={"keyword": keyword, "page": 1},
                selector="li.cds-grid-item a.cds-CommonCard-titleLink, .ais-InfiniteHits-item, .no-results, [data-e2e='no-results']",
                scroll_times=max(1, self.max_pages),
            )

    def parse(self, response: Response):
        """解析搜索结果页，产出 CourseItem。"""
        # Coursera 课程卡片选择器（2026 版本，需对照真实页面验证）
        # 新版用 li.cds-grid-item 包裹，内部 a.cds-CommonCard-titleLink 是标题
        cards = response.css("li.cds-grid-item:has(a.cds-CommonCard-titleLink)")

        if not cards:
            # 兼容旧版选择器
            cards = response.css("a.cds-CommonCard-titleLink, .ais-InfiniteHits-item")

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

        # 翻页（Coursera 搜索是无限滚动 + URL 翻页参数）
        current_page = response.meta.get("page", 1)
        self.logger.info(
            f"[coursera] kw={response.meta['keyword']} 页={current_page} 产出 {item_count} 条"
        )
        if current_page == 1 and response.meta.get("keyword") and item_count == 0:
            # browse 目录不含该定向词属预期（热门子集），宁空勿噪
            self.logger.info(
                f"[coursera] kw={response.meta['keyword']} browse 目录无相关课程，"
                f"定向补采请改用 edx/icourse163"
            )
        if current_page < self.max_pages:
            # Coursera 用 page=N 翻页（浏览页无 query 参数，须用 ? 而非 &）
            keyword = response.meta["keyword"]
            base = COURSERA_SEARCH_URL.format(keyword=quote(keyword)) if keyword else "https://www.coursera.org/browse"
            sep = "&" if "?" in base else "?"
            next_url = f"{base}{sep}page={current_page + 1}"
            yield make_playwright_request(
                next_url,
                meta={"keyword": keyword, "page": current_page + 1},
                selector="li.cds-grid-item a.cds-CommonCard-titleLink, .ais-InfiniteHits-item, .no-results, [data-e2e='no-results']",
                scroll_times=max(1, self.max_pages),
            )

    def _card_to_item(self, card, meta: dict) -> CourseItem:
        """将单个课程卡片转为 CourseItem。"""
        # 标题与链接（兼容两种卡片：div 包裹 a / a 元素自身）
        title_link = card.xpath(
            'self::a[contains(@class, "cds-CommonCard-titleLink")] | .//a[contains(@class, "cds-CommonCard-titleLink")]'
        )
        title = title_link.css("h3::text, h2::text, ::text").get(default="").strip()
        href = title_link.css("::attr(href)").get()

        if not title or not href:
            return None
        # 浏览页混入职业角色页（/career-academy/roles/...），非课程，跳过
        if "/career-academy/" in href:
            return None

        # href 可能是 /learn/{slug}（旧版）或 /search?query=...&xdpModal=course~{id}（新版 modal UX）
        # 从 href 提取 source_id 和 source_url
        source_id, source_url = self._extract_ids(href)
        # source_id 缺失时跳过（避免共用 "unknown" 常量导致 upsert 互相覆盖）
        if not source_id:
            return None

        # 院校/机构：cds-ProductCard-partnerNames（注意是 ProductCard 不是 CommonCard）
        institution = card.css(
            ".cds-ProductCard-partnerNames::text"
        ).get(default="").strip()

        # 评分（2026-08-14 实测改版：评分文本在 cds-RatingStat 内
        # "4.6 out of 5 stars"，不再挂在 aria-label/visually-hidden 上）：
        # 1) cds-RatingStat 文本（当前版）
        # 2) [data-testid="visually-hidden"] 内 "Rating, 4.6 out of 5 stars"（旧版）
        # 3) aria-label="4.6 out of 5 stars"（更旧版）
        rating_text = card.xpath(
            './/*[contains(@class, "RatingStat")]//text()[contains(., "out of 5 stars")]'
        ).get(default="")
        if not rating_text:
            rating_text = card.xpath(
                './/*[@data-testid="visually-hidden" and contains(text(), "Rating")]/text()'
            ).get(default="")
        if not rating_text:
            rating_text = card.xpath(
                './/*/@aria-label[contains(., "out of 5 stars")]'
            ).get(default="")
        rating = self._parse_rating(rating_text)

        # 评价数/注册数：新版 DOM 单独的 div 文本 "44K reviews"
        reviews_text = card.xpath(
            './/div[contains(text(), "reviews")]/text()'
        ).get(default="")
        enrollment = self._parse_reviews_count(reviews_text)

        # 元数据文本：cds-CommonCard-metadata 内的 p 标签
        # 新版："Beginner · Course · 1 - 3 Months"（无评分）
        # 旧版："★ 4.6 (44K) · Beginner · Course · 1 - 3 Months"（含评分）
        meta_text = card.css(".cds-CommonCard-metadata p::text").get(default="")
        duration, category, level = self._parse_metadata(meta_text)

        # 兜底：meta_text 内嵌 "★ 4.6 (18K)" 评分段时补提评分与评价数
        if rating == 0.0:
            rating = self._parse_rating(meta_text)
        if enrollment == 0:
            m = re.search(r"\(([\d.]+[KM]?)\)", meta_text)
            if m:
                enrollment = self._parse_reviews_count(f"{m.group(1)} reviews")

        # 技能：cds-CommonCard-bodyContent 内的 p 文本
        skills_text = card.css(".cds-CommonCard-bodyContent p::text").getall()
        skills = self._parse_skills(skills_text)

        # 关键词本地过滤（08-22）：/browse 无视 query 参数，定向采集时只有
        # 标题/院校/技能命中关键词的卡片才入库——否则热门课全量误入（当日实证）
        keyword = str(meta.get("keyword") or "")
        if keyword and not self._keyword_hit(keyword, title, institution, *skills):
            return None

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
        item["platform"] = "coursera"
        item["category"] = category
        item["rating"] = rating
        item["enrollment"] = enrollment
        item["duration"] = duration
        item["skills"] = skills
        item["raw_text"] = card.get()
        item["is_desensitized"] = False
        return item

    @staticmethod
    def _extract_ids(href: str) -> tuple:
        """从 href 提取 (source_id, source_url)。

        支持两种格式：
        - /learn/python-data → ('python-data', 'https://www.coursera.org/learn/python-data')
        - /search?query=Python&xdpModal=course~ejOz7RDUEei99hK0xs-tsg
          → ('ejOz7RDUEei99hK0xs-tsg', 'https://www.coursera.org/search?...&xdpModal=course~...')
        """
        course_url = urljoin(COURSERA_BASE, href)
        if "/learn/" in href:
            slug = href.rstrip("/").split("/learn/")[-1].split("?")[0]
            return slug, course_url
        # modal URL：从 xdpModal=course~{id} 或 xdpModal=course%7E{id} 提取
        m = re.search(r"xdpModal=course(?:~|%7E)([\w-]+)", href, re.IGNORECASE)
        if m:
            return m.group(1), course_url
        # 兜底：取 URL 最后一段（取不到稳定 ID 时返回 None，由调用方跳过）
        fallback = href.rstrip("/").split("/")[-1].split("?")[0]
        return (fallback, course_url) if fallback else (None, None)

    @staticmethod
    def _keyword_hit(keyword: str, *fields: str) -> bool:
        """关键词相关性判定：ASCII 词用词边界匹配，其余（CJK/含符号）子串匹配。

        词边界防子串误命中（"air" ⊂ "hair"）；空关键词 = 全通过（热门课全量模式）。
        """
        if not keyword:
            return True
        hay = " ".join(f for f in fields if f).lower()
        if not hay:
            return False
        kw = keyword.strip().lower()
        if kw.isascii() and re.fullmatch(r"[a-z0-9][a-z0-9\s-]*", kw):
            return re.search(rf"\b{re.escape(kw)}\b", hay) is not None
        return kw in hay

    @staticmethod
    def _parse_rating(text: str) -> float:
        """从 'Rating, 4.6 out of 5 stars'、'4.6 out of 5 stars' 或 '★ 4.6 (18K)' 提取评分。"""
        if not text:
            return 0.0
        m = re.search(r"([\d.]+)\s*out of", text) or re.search(r"★\s*([\d.]+)", text)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
        return 0.0

    @staticmethod
    def _parse_reviews_count(text: str) -> int:
        """从 '44K reviews' 或 '1.2M reviews' 提取评价数。"""
        if not text:
            return 0
        m = re.search(r"([\d.]+)\s*([KM]?)\s*reviews", text, re.IGNORECASE)
        if not m:
            return 0
        try:
            num = float(m.group(1))
            suffix = m.group(2).upper()
            if suffix == "K":
                return int(num * 1000)
            elif suffix == "M":
                return int(num * 1000000)
            return int(num)
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _parse_metadata(text: str) -> tuple:
        """解析 'Beginner · Course · 1 - 3 Months' 风格的元数据。

        返回 (duration, category, level)。
        """
        duration = ""
        category = ""
        level = ""

        if not text:
            return duration, category, level

        parts = [p.strip() for p in text.split("·") if p.strip()]
        for part in parts:
            if "★" in part or "Rating" in part:
                continue  # 评分段（旧版 "★ 4.6 (18K)"），不污染 duration
            if not level and part in ("Beginner", "Intermediate", "Advanced", "Mixed"):
                level = part
            elif not category and part in (
                "Course", "Specialization", "Professional Certificate",
                "Guided Project", "Degree", "Course Series",
            ):
                category = part
            elif not duration:
                duration = part

        return duration, category, level

    @staticmethod
    def _parse_skills(texts: list) -> list:
        """从 'Skills you'll gain: A, B, C' 文本中提取技能列表。"""
        for text in texts:
            text = str(text).strip()
            # 跳过 "Skills you'll gain: " 这种标签前缀
            if "skills you'll gain" in text.lower():
                # 提取冒号后的内容
                m = re.search(r":\s*(.+)", text, re.IGNORECASE)
                if m:
                    skills_str = m.group(1).strip()
                    return [s.strip() for s in skills_str.split(",") if s.strip()]
            # 整段都是技能的兜底
            elif "," in text and len(text) > 10:
                return [s.strip() for s in text.split(",") if s.strip()]
        return []

    @staticmethod
    def _parse_count(text: str) -> int:
        """解析 '44K' / '1.2M' / '12345' 风格的数字。"""
        text = str(text).strip().lower().replace(",", "")
        m = re.search(r"([\d.]+)\s*([km]?)", text)
        if not m:
            return 0
        try:
            num = float(m.group(1))
            suffix = m.group(2)
            if suffix == "k":
                return int(num * 1000)
            elif suffix == "m":
                return int(num * 1000000)
            return int(num)
        except (ValueError, TypeError):
            return 0

