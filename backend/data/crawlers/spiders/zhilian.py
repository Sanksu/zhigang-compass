"""智联招聘爬虫（A 级 — 稳定源）。

策略：
- Playwright 渲染 JS 搜索页
- 搜索列表页 → 详情页 → 提取结构化字段
- 单 IP 直连，限速 20 req/min（delay_range 2-5s）

⚠️ 合规提醒：仅采集公开搜索页。CSS 选择器需对照真实页面验证。
运行：scrapy crawl zhilian -a keywords=Python -a cities=北京 -o output/zhilian.jsonl
"""

import json
import re

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
                city_code = ZHILIAN_CITY_CODES.get(city)
                if not city_code:
                    self.logger.warning(f"跳过未映射城市: {city}（智联城市码表仅含 {list(ZHILIAN_CITY_CODES)}）")
                    continue
                url = f"{self.SEARCH_URL}?{self.build_query({'jl': city_code, 'kw': keyword, 'pn': 1})}"
                yield self._make_playwright_request(
                    url,
                    meta={"keyword": keyword, "city": city, "page": 1},
                )

    def _extract_publish_time_map(self, response: Response) -> dict:
        """从 SSR __INITIAL_STATE__ 提取 number → publishTime 映射。

        智联列表页 DOM 不渲染发布日期，但 SSR 数据含 publishTime 字段。
        """
        # 提取 __INITIAL_STATE__ 的 JSON 内容
        script_text = response.css("script:not([src])::text").getall()
        publish_map = {}
        for text in script_text:
            if "__INITIAL_STATE__" not in text or "publishTime" not in text:
                continue
            # __INITIAL_STATE__={...} 格式，截取 JSON 部分（非贪婪 + 尾部锚定，避免贪婪吞掉后续 JS）
            match = re.search(r"__INITIAL_STATE__\s*=\s*(\{.*\})\s*;?\s*$", text, re.DOTALL)
            if not match:
                continue
            try:
                data = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            # 遍历 jobList 提取 number → publishTime
            self._walk_for_publish_time(data, publish_map)
            break
        return publish_map

    def _walk_for_publish_time(self, obj, publish_map: dict):
        """递归遍历 SSR JSON，提取 number 与 publishTime 的配对。"""
        if isinstance(obj, dict):
            number = obj.get("number")
            publish_time = obj.get("publishTime")
            if number and publish_time:
                publish_map[number] = publish_time
            for v in obj.values():
                self._walk_for_publish_time(v, publish_map)
        elif isinstance(obj, list):
            for item in obj:
                self._walk_for_publish_time(item, publish_map)

    def parse(self, response: Response):
        """解析搜索列表页，直接产出 JobItem。

        智联详情页有验证码反爬，短时间内批量访问会触发 Security Verification。
        因此列表页直接产出 Item（含 title/company/salary/经验/学历/标签），
        description 和 requirements 留空。详情页采集作为后续优化项。
        """
        # 提取 SSR 中的 number → publishTime 映射（DOM 不渲染发布日期）
        publish_time_map = self._extract_publish_time_map(response)

        cards = response.css(".joblist-box__item")

        if not cards:
            self.logger.warning(
                f"列表页无岗位卡片（kw={response.meta.get('keyword')}），"
                f"页面标题: {response.css('title::text').get(default='')}"
            )
            return

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

            # 发布日期：从 SSR __INITIAL_STATE__ 提取（DOM 不渲染）
            post_date = publish_time_map.get(source_id, "")

            # raw_text：列表页无详情正文，拼接元数据作为 LLM 抽取输入
            # 智联详情页有验证码反爬，短时间内批量访问会触发 Security Verification
            raw_text = "\n".join([
                f"岗位名称：{title}",
                f"公司：{company}",
                f"工作地点：{location}",
                f"薪资：{salary}",
                f"经验要求：{experience}",
                f"学历要求：{education}",
                f"技能标签：{', '.join(tags)}",
            ])

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
                raw_text=raw_text,
                post_date=post_date,
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

    def _make_playwright_request(self, url: str, meta: dict):
        """构造 Playwright 渲染请求，等待列表卡片加载。"""
        from scrapy.http import Request
        return Request(
            url,
            callback=self.parse,
            meta={
                "playwright": True,
                "playwright_page_methods": [
                    PageMethod("wait_for_selector", ".joblist-box__item", timeout=15000),
                    PageMethod("evaluate", "window.scrollTo(0, document.body.scrollHeight)"),
                ],
                **meta,
            },
            headers=self._compliance_headers(),
            dont_filter=True,
        )
