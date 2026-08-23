"""智联招聘爬虫（A 级 — 稳定源）。

策略：
- Playwright 渲染 JS 搜索页
- 搜索列表页 → 详情页 → 提取结构化字段
- 单 IP 直连，限速 5 req/min（delay_range 8-15s，设计 §4 国内平台间隔）

⚠️ 合规提醒：仅采集公开搜索页。CSS 选择器需对照真实页面验证。
运行：scrapy crawl zhilian -a keywords=Python -a cities=北京 -o output/zhilian.jsonl
"""

import json
from datetime import datetime, timedelta, timezone

from scrapy.exceptions import CloseSpider
from scrapy.http import Response

from crawlers.base_spider import make_playwright_request, BaseSpider
from crawlers.middlewares import backoff_delay
from crawlers.settings import CRAWL_ITEMS_CAP
from crawlers.zhilian_detail import extract_job_detail

# 页面级空列表退避重试（08-21c zhilian 反爬加固）：列表页 200 但无岗位卡片时，
# 判定为智联临时风控/跳验证页——现有 BackoffRetry 只处理 HTTP 429/403，不覆盖
# 渲染后空列表场景。此处按 max_empty_retries（默认 3）指数退避（30/60/120s，
# 复用 crawlers.middlewares.backoff_delay）重发同一搜索 URL；0=关闭。
DEFAULT_MAX_EMPTY_RETRIES = 3


def _call_later(delay, callback, *args, **kwargs):
    """延迟调度回调；仅在实际重试时加载 Twisted reactor。"""
    from twisted.internet import reactor

    return reactor.callLater(delay, callback, *args, **kwargs)


def _max_empty_retries() -> int:
    """读取爬虫级 max_empty_retries 配置（0=关闭），失败回退默认 3。"""
    try:
        from app.core import runtime_config

        cfg = runtime_config.get("crawlers") or {}
        mer = (cfg.get("zhilian") or {}).get("max_empty_retries")
        if mer is None:
            return DEFAULT_MAX_EMPTY_RETRIES
        return mer
    except Exception:
        return DEFAULT_MAX_EMPTY_RETRIES

# 智联城市代码映射
ZHILIAN_CITY_CODES = {
    "北京": "530", "上海": "538", "深圳": "765",
    "杭州": "539", "广州": "763", "成都": "801",
    "南京": "635", "武汉": "736",
}

# 增量采集翻页上限（默认）；历史回爬放宽（G-01，由发布时间截断提前停页）
INCREMENTAL_MAX_PAGES = 5
BACKFILL_MAX_PAGES = 50

# 东八区
_CST = timezone(timedelta(hours=8))


def _extract_initial_state(text: str) -> dict | None:
    """从 script 文本提取 __INITIAL_STATE__ 后的完整 JSON 对象（H4 修复）。

    从 `__INITIAL_STATE__=` 之后的第一个 `{` 开始做花括号配平（跳过字符串内
    的花括号），直到配平的 `}` 结束，再 json.loads。相比贪婪正则（匹配 `{...}`
    到行尾）：script 内 JSON 后仍跟其它 JS 内容时不会被误吞，嵌套对象也正确。

    找不到 / 解析失败返回 None。
    """
    marker = "__INITIAL_STATE__"
    idx = text.find(marker)
    if idx == -1:
        return None
    start = text.find("{", idx)
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    return None
    return None


def _is_older_than_days(post_date: str, days: int) -> bool:
    """发布时间是否早于 days 天前。无法解析返回 False（不误截断）。"""
    try:
        dt = datetime.fromisoformat(post_date)
    except (ValueError, TypeError):
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_CST)
    cutoff = datetime.now(_CST) - timedelta(days=days)
    return dt < cutoff


class ZhilianSpider(BaseSpider):
    name = "zhilian"
    platform = "zhilian"

    # 入口保持 sou.zhaopin.com：www.zhaopin.com 的 robots.txt 已被 EdgeOne 验证页
    # 拦截（Protego 解析出 DENY，2026-08 实证 robotstxt/forbidden），sou 域放行；
    # 请求落地后 302 至新版 www.zhaopin.com/jobs（Playwright 导航内跳转，不在
    # Scrapy robots 过滤范围），SSR positionList 提供岗位数据。
    SEARCH_URL = "https://sou.zhaopin.com/"

    # 页面就绪信号（岗位数据统一从 SSR positionList 提取，见 parse）：
    # 智联现分流两套搜索页——新版 www.zhaopin.com/jobs（.job-card）与
    # sou 版 www.zhaopin.com/sou/jl489/kw.../p1（jobdetail 链接），
    # 任一出现即视为就绪（2026-08 改版实证）。
    LIST_SELECTOR = "[class*='job-card'], a[href*='jobdetail']"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 历史回爬（G-01）：-a history_days=90 放宽翻页上限并逐页按发布时间截断
        self.history_days = int(kwargs.get("history_days") or 0)
        self._max_pages = BACKFILL_MAX_PAGES if self.history_days else INCREMENTAL_MAX_PAGES
        # JD 采集条数上限（-a max_results=200，默认 200）：按产出条数截断，
        # 防列表页全量遍历超长运行（08-13 实测 zhilian 挂死 8h，900s 超时后
        # 孤儿爬虫仍残留；条数上限在源头截断，与页数上限/900s 超时三重保险）
        self._max_results = int(kwargs.get("max_results") or CRAWL_ITEMS_CAP)
        self._items_collected = 0
        # 页面级空列表退避重试（08-21c）：0=关闭；>0 时列表页 200 但无卡片按
        # 指数退避重发（防智联临时风控/跳验证页）。计数跨列表页累计，防单次
        # 采集被"每页都空列表"无限重试拖长运行。
        self._max_empty_retries = _max_empty_retries()
        self._empty_retries_used = 0

    def _bump_items(self):
        """产出计数：达到 max_results 上限即关闭爬虫（CloseSpider 停止新调度）。"""
        self._items_collected += 1
        if self._items_collected >= self._max_results:
            raise CloseSpider(f"达到 JD 采集上限 {self._max_results} 条")

    def _cutoff_reached(self, publish_time_map: dict) -> bool:
        """本页出现早于 history_days 天的岗位时停止翻页（列表按发布时间倒序）。"""
        if not self.history_days:
            return False
        return any(_is_older_than_days(v, self.history_days) for v in publish_time_map.values())

    def start_requests(self):
        # 空关键词/空城市 = 平台默认推荐列表且不限城市（08-16 用户决策）
        keywords = self.keywords or [""]
        cities = self.cities or [""]
        for keyword in keywords:
            for city in cities:
                city_code = ZHILIAN_CITY_CODES.get(city)
                if city and not city_code:
                    self.logger.warning(f"跳过未映射城市: {city}（智联城市码表仅含 {list(ZHILIAN_CITY_CODES)}）")
                    continue
                params = {"kw": keyword, "pn": 1}
                if city_code:
                    params["jl"] = city_code
                url = f"{self.SEARCH_URL}?{self.build_query(params)}"
                yield make_playwright_request(
                    url,
                    meta={"keyword": keyword, "city": city, "page": 1},
                    selector=self.LIST_SELECTOR,
                    wait_timeout=15000,
                    scroll_times=1,
                    scroll_wait_ms=0,
                    headers=self._compliance_headers(),
                )

    def _extract_list_state(self, response: Response) -> tuple[list, dict, bool]:
        """从 SSR __INITIAL_STATE__ 提取 positionList / number→publishTime / hasMore。

        2026-08 改版后岗位数据直接随 SSR positionList 提供（含标题/公司/薪资/
        经验/学历/技能标签/详情 URL/发布时间），DOM 卡片仅展示用，不再递归遍历
        script 找 publishTime（旧版 sou.zhaopin.com 结构）。
        """
        for text in response.css("script:not([src])::text").getall():
            if "__INITIAL_STATE__" not in text:
                continue
            data = _extract_initial_state(text)
            if data is None:
                continue
            position_list = data.get("positionList") or []
            publish_map = {
                str(j.get("number")): (j.get("publishTime") or "")
                for j in position_list
                if j.get("number")
            }
            return position_list, publish_map, bool(data.get("hasMore"))
        return [], {}, False

    def parse(self, response: Response):
        """解析搜索列表页，为每条岗位发出详情请求。

        列表页仅有标题/公司/薪资/经验/学历/技能标签与详情 URL（SSR positionList，
        2026-08 改版后 DOM 卡片不渲染完整字段；正文在详情页 SSR，列表页
        jobDescription 仅摘要）。对每条岗位发详情请求解析正文；详情失败
        （验证码拦截等）经 errback 降级为列表页摘要产出，不丢数据。
        """
        position_list, publish_time_map, has_more = self._extract_list_state(response)

        if not position_list:
            # 页面级空列表退避重试（08-21c）：200 但无岗位数据 = 智联临时风控/跳验证页。
            # 未用尽重试额度时按 backoff_delay 延迟重发同一搜索 URL（reactor 调度、
            # 不阻塞事件循环）；用尽则记录并按原逻辑跳过。
            if self._max_empty_retries > 0 and self._empty_retries_used < self._max_empty_retries:
                retry_n = self._empty_retries_used
                self._empty_retries_used += 1
                delay = backoff_delay(retry_n)
                self.logger.warning(
                    f"[zhilian] 列表页无岗位数据（kw={response.meta.get('keyword')} "
                    f"页={response.meta.get('page')}），指数退避 {delay}s 后重试 "
                    f"（{self._empty_retries_used}/{self._max_empty_retries}）",
                )
                retry_request = make_playwright_request(
                    response.url,
                    meta={
                        "keyword": response.meta.get("keyword"),
                        "city": response.meta.get("city"),
                        "page": response.meta.get("page", 1),
                    },
                    selector=self.LIST_SELECTOR,
                    wait_timeout=15000,
                    scroll_times=1,
                    scroll_wait_ms=0,
                    headers=self._compliance_headers(),
                )
                # make_playwright_request 已设 dont_filter=True，重发同一 URL 不会被去重。
                # _call_later 仅在实际重试时才加载 reactor，避免 SpiderLoader 预加载时
                # 安装默认 reactor；不捕获调度异常，保持其向调用方传播。
                _call_later(delay, self.crawler.engine.schedule, retry_request, self)
                return
            self.logger.warning(
                f"[zhilian] 列表页无岗位数据（kw={response.meta.get('keyword')} 页={response.meta.get('page')}），"
                f"页面标题: {response.css('title::text').get(default='')}"
            )
            return

        yield_count = 0
        for j in position_list:
            title = (j.get("name") or "").strip()
            number = str(j.get("number") or "")
            if not title or not number:
                continue
            company = (j.get("companyName") or "").strip()
            salary = (j.get("salary60") or j.get("salaryReal") or "").strip()
            # 地点：工作城市 + 区县（DOM 卡片同源字段）
            location = " ".join(
                x for x in [j.get("workCity") or "", j.get("cityDistrict") or ""] if x
            )
            experience = (j.get("workingExp") or "").strip()
            education = (j.get("education") or "").strip()
            # 技能标签：SSR 为 {id,name,standard} 列表
            tags = [t.get("name") for t in (j.get("jobSkillTags") or []) if t.get("name")]

            detail_url = (j.get("positionUrl") or "").strip()
            if not detail_url:
                continue
            # 详情 URL 剥离追踪参数（refcode/srccode/preactionid）：
            # 智联 robots.txt 用 `Disallow: /*?*` 拒绝一切带 query 的请求
            # （Protego 匹配 path+query），scrapy ROBOTSTXT_OBEY 会直接丢弃，
            # 导致详情页全部走 errback 降级为列表页摘要、正文缺失。
            clean_href = detail_url.split("?")[0]
            detail_url = response.urljoin(clean_href)
            source_id = number

            # 发布日期：SSR positionList 随条目提供（DOM 不渲染）
            post_date = publish_time_map.get(number, "")

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

        # 翻页（默认最多 5 页；历史回爬放宽并由发布时间截断提前停页）
        current_page = response.meta.get("page", 1)
        self.logger.info(
            f"[zhilian] kw={response.meta.get('keyword')} city={response.meta.get('city')} "
            f"页={current_page} 产出 {yield_count} 条详情请求"
        )
        if (
            current_page < self._max_pages
            and has_more
            and not self._cutoff_reached(publish_time_map)
        ):
            # 新版翻页：SSR hasMore 驱动，pn 递增直构 URL（旧版 DOM 翻页链接已不存在）
            city_code = ZHILIAN_CITY_CODES.get(response.meta.get("city", ""))
            params = {"kw": response.meta.get("keyword", ""), "pn": current_page + 1}
            if city_code:
                params["jl"] = city_code
            next_url = f"{self.SEARCH_URL}?{self.build_query(params)}"
            yield make_playwright_request(
                next_url,
                meta={
                    "keyword": response.meta["keyword"],
                    "city": response.meta["city"],
                    "page": current_page + 1,
                },
                selector=self.LIST_SELECTOR,
                wait_timeout=15000,
                scroll_times=1,
                scroll_wait_ms=0,
                headers=self._compliance_headers(),
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

        self._bump_items()  # 条数上限（默认 200）截断，防超长运行
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
        self._bump_items()  # 条数上限同样约束降级路径
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

