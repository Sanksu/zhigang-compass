"""智联招聘爬虫（A 级 — 稳定源）。

策略：
- Playwright 渲染 JS 搜索页
- 搜索列表页 → 详情页 → 提取结构化字段
- 单 IP 直连，限速 5 req/min（delay_range 8-15s，设计 §4 国内平台间隔）

⚠️ 合规提醒：仅采集公开搜索页。CSS 选择器需对照真实页面验证。
运行：scrapy crawl zhilian -a keywords=Python -a cities=北京 -o output/zhilian.jsonl
"""

import json
import re

from scrapy.http import Response
from scrapy_playwright.page import PageMethod

from crawlers.base_spider import BaseSpider
from crawlers.zhilian_detail import extract_job_detail

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
        """解析搜索列表页，为每条岗位发出详情请求。

        列表页仅有 title/company/salary/经验/学历/标签（详情页有验证码反爬，
        不再批量直抓），正文（岗位职责/任职要求）在详情页 SSR __INITIAL_STATE__
        内。对每条岗位发详情请求解析正文；详情失败（验证码拦截等）经 errback
        降级为列表页摘要产出，不丢数据。
        """
        # 提取 SSR 中的 number → publishTime 映射（DOM 不渲染发布日期）
        publish_time_map = self._extract_publish_time_map(response)

        cards = response.css(".joblist-box__item")

        if not cards:
            self.logger.warning(
                f"[zhilian] 列表页无岗位卡片（kw={response.meta.get('keyword')} 页={response.meta.get('page')}），"
                f"页面标题: {response.css('title::text').get(default='')}"
            )
            return

        yield_count = 0
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

            # 列表页摘要：详情页解析失败时作为 raw_text 兜底（正文在详情页）
            raw_text = "\n".join([
                f"岗位名称：{title}",
                f"公司：{company}",
                f"工作地点：{location}",
                f"薪资：{salary}",
                f"经验要求：{experience}",
                f"学历要求：{education}",
                f"技能标签：{', '.join(tags)}",
            ])

            yield_count += 1
            yield self._make_detail_request(detail_url, {
                "source_id": source_id,
                "source_url": detail_url,
                "title": title,
                "company": company,
                "location": location,
                "salary": salary,
                "experience": experience,
                "education": education,
                "tags": tags,
                "post_date": post_date,
                "raw_text": raw_text,
            })

        # 翻页（最多 5 页，避免过度采集触发反爬）
        current_page = response.meta.get("page", 1)
        self.logger.info(
            f"[zhilian] kw={response.meta.get('keyword')} city={response.meta.get('city')} "
            f"页={current_page} 产出 {yield_count} 条详情请求"
        )
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

    def _make_detail_request(self, url: str, job: dict):
        """构造详情页请求（普通 HTTP，SSR 已含正文，无需渲染）。"""
        from scrapy.http import Request
        return Request(
            url,
            callback=self.parse_detail,
            meta={"job": job},
            errback=self._detail_errback,
            dont_filter=True,
        )

    def parse_detail(self, response: Response):
        """详情页回调：解析 SSR 正文，补全 description/requirements 后产出 Item。"""
        job = response.meta["job"]
        detail = extract_job_detail(response.text)
        description, requirements = detail["description"], detail["requirements"]

        # 正文追加到列表页摘要后（正文为空时保持摘要兜底）
        raw_text = job["raw_text"]
        if description or requirements:
            raw_text = "\n".join([raw_text, description, requirements])

        yield self.make_item(
            source_id=job["source_id"],
            source_url=job["source_url"],
            title=job["title"],
            company=job["company"],
            location=job["location"],
            salary=job["salary"],
            experience=job["experience"],
            education=job["education"],
            tags=job["tags"],
            description=description,
            requirements=requirements,
            raw_text=raw_text,
            post_date=job["post_date"],
        )

    def _detail_errback(self, failure):
        """详情页失败（验证码拦截/超时）降级为列表页摘要，避免整条丢失。"""
        job = failure.request.meta["job"]
        self.logger.warning(
            f"[zhilian] 详情页获取失败，降级列表页摘要: {failure.value}"
        )
        yield self.make_item(
            source_id=job["source_id"],
            source_url=job["source_url"],
            title=job["title"],
            company=job["company"],
            location=job["location"],
            salary=job["salary"],
            experience=job["experience"],
            education=job["education"],
            tags=job["tags"],
            description="",
            requirements="",
            raw_text=job["raw_text"],
            post_date=job["post_date"],
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
