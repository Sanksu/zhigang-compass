"""智联招聘爬虫（A 级 — 稳定源）。

策略：
- Playwright 渲染 JS 搜索页
- 搜索列表页 → 详情页 → 提取结构化字段
- 单 IP 直连，限速 20 req/min（delay_range 2-5s）

⚠️ 合规提醒：仅采集公开搜索页。CSS 选择器需对照真实页面验证。
运行：scrapy crawl zhilian -a keywords=Python -a cities=北京 -o output/zhilian.jsonl
"""

from scrapy.http import Response
from scrapy_playwright.page import PageMethod

from crawlers.base_spider import BaseSpider

# 智联城市代码映射
ZHILIAN_CITY_CODES = {
    "北京": "530", "上海": "538", "深圳": "765",
    "杭州": "539", "广州": "763", "成都": "801",
    "南京": "635", "武汉": "736",
}


class ZhilianSpider(BaseSpider):
    name = "zhilian"
    platform = "zhilian"

    SEARCH_URL = "https://sou.zhaopin.com/"

    def start_requests(self):
        for keyword in self.keywords:
            for city in self.cities:
                city_code = ZHILIAN_CITY_CODES.get(city, city)
                url = f"{self.SEARCH_URL}?{self.build_query({'jl': city_code, 'kw': keyword, 'pn': 1})}"
                yield self._make_playwright_request(
                    url,
                    meta={"keyword": keyword, "city": city, "page": 1},
                )

    def parse(self, response: Response):
        """解析搜索列表页，直接产出 JobItem。

        智联详情页有验证码反爬，短时间内批量访问会触发 Security Verification。
        因此列表页直接产出 Item（含 title/company/salary/经验/学历/标签），
        description 和 requirements 留空。详情页采集作为后续优化项。
        """
        cards = response.css(".joblist-box__item")

        for card in cards:
            title = card.css(".jobinfo__name::text").get(default="").strip()
            company = card.css(".companyinfo__name::text").get(default="").strip()
            salary = card.css(".jobinfo__salary::text").get(default="").strip()

            # 地点/经验/学历在 .jobinfo__other-info-item 中
            info_items = card.css(".jobinfo__other-info-item")
            location = info_items[0].css("span::text").get(default="").strip() if len(info_items) > 0 else ""
            experience = info_items[1].css("::text").get(default="").strip() if len(info_items) > 1 else ""
            education = info_items[2].css("::text").get(default="").strip() if len(info_items) > 2 else ""

            # 技术标签（FASTAPI / Flask 等）
            tags = [t.strip() for t in card.css(".jobinfo__tag .joblist-box__item-tag::text").getall() if t.strip()]

            detail_href = card.css(".jobinfo__name::attr(href)").get()
            if not detail_href or not title:
                continue
            detail_url = response.urljoin(detail_href)
            source_id = detail_href.rstrip("/").split("/")[-1].split(".")[0].split("?")[0]

            yield self.make_item(
                source_id=source_id,
                source_url=detail_url,
                title=title,
                company=company,
                location=location,
                salary=salary,
                experience=experience,
                education=education,
                tags=tags,
                description="",
                requirements="",
                raw_text="",
            )

        # 翻页（最多 5 页，避免过度采集触发反爬）
        current_page = response.meta.get("page", 1)
        if current_page < 5:
            next_href = response.css(".next-page::attr(href), a.pageset[rel=next]::attr(href)").get()
            if next_href:
                next_url = response.urljoin(next_href)
                yield self._make_playwright_request(
                    next_url,
                    meta={
                        "keyword": response.meta["keyword"],
                        "city": response.meta["city"],
                        "page": current_page + 1,
                    },
                )

    def parse_detail(self, response: Response):
        """解析岗位详情页。"""
        list_meta = response.meta["list_meta"]

        # 详情页正文
        detail_text = " ".join(
            response.css(".describtion__detail-content *::text, .job-detail-content *::text").getall()
        ).strip()

        # 职责与要求分段
        description = ""
        requirements = ""
        for sep in ("任职要求", "任职资格", "岗位要求"):
            if sep in detail_text:
                parts = detail_text.split(sep, 1)
                description = parts[0].strip()
                requirements = parts[1].strip()
                break
        else:
            description = detail_text

        yield self.make_item(
            source_id=list_meta["source_id"],
            source_url=list_meta["source_url"],
            title=list_meta["title"],
            company=list_meta["company"],
            location=list_meta["location"],
            salary=list_meta["salary"],
            experience=list_meta["experience"],
            education=list_meta["education"],
            description=description,
            requirements=requirements,
            raw_text=detail_text,
        )

    def _make_playwright_request(self, url: str, meta: dict, callback=None):
        """构造 Playwright 渲染请求。列表页等卡片，详情页等正文。"""
        from scrapy.http import Request
        is_detail = callback is not None and getattr(callback, "__name__", "") == "parse_detail"
        wait_selector = ".describtion__detail-content, .job-detail-content, .job-detail" if is_detail else ".joblist-box__item"
        return Request(
            url,
            callback=callback or self.parse,
            meta={
                "playwright": True,
                "playwright_page_methods": [
                    PageMethod("wait_for_selector", wait_selector, timeout=15000),
                    PageMethod("evaluate", "window.scrollTo(0, document.body.scrollHeight)"),
                ],
                **meta,
            },
            headers=self._compliance_headers(),
            dont_filter=True,
        )
