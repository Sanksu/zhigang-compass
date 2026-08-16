"""GitHub Trending 爬虫（技术热点观察池数据源）。

策略：
- 爬取 GitHub Search API（官方 API 条款允许程序化访问；trending 网页 robots
  Disallow: / 且无官方 API——用 search/repositories created:>窗口 sort=stars 近似）
- 默认全局热度模式（08-16 用户决策）：不限语言，取窗口内 star 最高的 100 个
  仓库；可通过 -a languages= 覆盖为按语言过滤（每语言 20 个）
- 产出 CommunityTrendItem（trend_type=trending）

合规：
- 仅采集 API 返回的仓库元数据（full_name/描述/star/fork/language）
- 无 token 限 10 req/min（全局模式 1 请求 / 语言模式 5 请求，请求间隔保留）

运行：
  scrapy crawl github -a since=daily -o output/github.jsonl
  # 国际源，需代理
  $env:HTTPS_PROXY="http://127.0.0.1:7890"
"""

from datetime import datetime, timedelta, timezone

from scrapy import Request, Spider
from scrapy.http import Response

import json
from urllib.parse import quote

from crawlers.items import CommunityTrendItem
from crawlers.settings import CRAWL_ITEMS_CAP, RATE_LIMIT


GITHUB_SEARCH_API = "https://api.github.com/search/repositories"

# 默认时间窗口（daily/weekly/monthly）
DEFAULT_SINCE = "daily"

# 默认语言过滤：空 = 全局热度（不限语言，窗口内 star 最高 top 100）。
# 传 -a languages=python,java 时按语言分别取热度（每语言 20，合计 ≤100）
DEFAULT_LANGUAGES: list[str] = []


class GithubSpider(Spider):
    """GitHub Trending 公开页采集。

    不继承 BaseSpider（非岗位数据），直接继承 Spider。
    """

    name = "github"
    platform = "github"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # -a languages=python,java 覆盖默认语言
        langs = kwargs.get("languages")
        self.languages = langs.split(",") if langs else DEFAULT_LANGUAGES
        # -a since=daily|weekly|monthly
        self.since = kwargs.get("since", DEFAULT_SINCE)
        # 请求间隔
        limit = RATE_LIMIT.get(self.platform, {})
        delay_range = limit.get("delay_range", (10, 20))
        self.download_delay = sum(delay_range) / 2

    async def start(self):
        """Scrapy 2.13+ 入口：桥接到 start_requests。"""
        for request in self.start_requests():
            yield request

    def start_requests(self):
        # since 语义 → created 窗口：daily=1 天 / weekly=7 天 / monthly=30 天
        window = {"daily": 1, "weekly": 7, "monthly": 30}.get(self.since, 7)
        created = (datetime.now(timezone(timedelta(hours=8))).date()
                   - timedelta(days=window)).isoformat()

        if not self.languages:
            # 全局热度：不限语言，窗口内 star 最高的 100 个仓库（单次采集上限内）
            query = f"created:>{created}"
            url = f"{GITHUB_SEARCH_API}?q={quote(query)}&sort=stars&order=desc&per_page={min(CRAWL_ITEMS_CAP, 100)}"
            self.logger.info(f"开始采集 GitHub 全局热门 (created>{created})")
            yield Request(
                url,
                callback=self.parse,
                meta={"language": "", "since": self.since, "dont_obey_robotstxt": True},
                headers={
                    "User-Agent": "zhigang-compass/1.0 (github-api)",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            return

        for lang in self.languages:
            query = f"language:{lang} created:>{created}"
            url = f"{GITHUB_SEARCH_API}?q={quote(query)}&sort=stars&order=desc&per_page=20"
            self.logger.info(f"开始采集 GitHub 热门: {lang} (created>{created})")
            yield Request(
                url,
                callback=self.parse,
                # API 例外（08-14 用户确认 B 方案）：api.github.com 官方 API 条款
                # 允许合理程序化访问（robots 保守规则不适用 API 端点）
                meta={"language": lang, "since": self.since, "dont_obey_robotstxt": True},
                headers={
                    "User-Agent": "zhigang-compass/1.0 (github-api)",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )

    def parse(self, response: Response):
        """解析 GitHub Search API 响应（JSON），产出 CommunityTrendItem。"""
        try:
            data = json.loads(response.text)
        except ValueError as e:
            self.logger.error(f"GitHub API 响应解析失败（language={response.meta['language']}）: {e}")
            return
        items = data.get("items") or []
        if not items:
            self.logger.warning(
                f"GitHub API 无结果（language={response.meta['language']}），"
                f"message: {data.get('message', '')[:120]}"
            )
            return
        for repo in items:
            item = self._api_to_item(repo, response.meta)
            if item:
                yield item
        self.logger.info(
            f"GitHub API [{response.meta['language']}/{response.meta['since']}] "
            f"采集 {len(items)} 个仓库"
        )

    def _api_to_item(self, repo: dict, meta: dict) -> CommunityTrendItem:
        """将 GitHub Search API 仓库对象转为 CommunityTrendItem。"""
        full_name = repo.get("full_name", "")
        if not full_name:
            return None
        language = repo.get("language") or meta.get("language", "")
        item = CommunityTrendItem()
        item["source"] = self.platform
        item["source_id"] = full_name  # owner/repo 作为唯一 ID
        item["source_url"] = repo.get("html_url", f"https://github.com/{full_name}")
        item["crawled_at"] = datetime.now(timezone(timedelta(hours=8))).isoformat()
        item["title"] = full_name
        item["description"] = (repo.get("description") or "").strip()
        item["url"] = item["source_url"]
        item["stars"] = repo.get("stargazers_count", 0)
        item["forks"] = repo.get("forks_count", 0)
        item["stars_today"] = 0  # Search API 无"今日新增 star"，置 0
        item["language"] = language
        item["tags"] = [language] if language else []
        item["trend_type"] = "trending"
        return item

    def _card_to_item(self, card, meta: dict) -> CommunityTrendItem:
        """将单个仓库卡片转为 CommunityTrendItem。"""
        # 仓库全名：h2 > a 的 href 属性（如 /owner/repo）
        repo_href = card.css("h2 a::attr(href)").get()
        if not repo_href:
            return None

        # owner/repo 格式
        repo_path = repo_href.strip("/")
        repo_name = repo_path.split("/")[-1] if "/" in repo_path else repo_path
        repo_full_name = repo_path  # owner/repo
        repo_url = f"https://github.com{repo_href}"

        # 描述
        description = card.css("p::text").get(default="").strip()

        # 编程语言
        language = card.css('span[itemprop="programmingLanguage"]::text').get(
            default=meta.get("language", "")
        ).strip()

        # 总 star 数：a[href$="/stargazers"] 的文本
        stars_text = card.css('a[href*="/stargazers"]::text').getall()
        stars = self._parse_count(stars_text)

        # 总 fork 数：a[href$="/forks"] 或 a[href$="/members"] 的文本
        forks_text = card.css('a[href*="/forks"]::text, a[href*="/members"]::text').getall()
        forks = self._parse_count(forks_text)

        # 今日新增 star：span.d-inline-block.float-sm-right 或类似
        stars_today_text = card.css("span.d-inline-block.float-sm-right::text").getall()
        stars_today = self._parse_count(stars_today_text)

        item = CommunityTrendItem()
        item["source"] = self.platform
        item["source_id"] = repo_full_name  # owner/repo 作为唯一 ID
        item["source_url"] = repo_url
        item["crawled_at"] = datetime.now(timezone(timedelta(hours=8))).isoformat()
        item["title"] = repo_full_name
        item["description"] = description
        item["url"] = repo_url
        item["stars"] = stars
        item["forks"] = forks
        item["stars_today"] = stars_today
        item["language"] = language
        item["tags"] = [language] if language else []
        item["trend_type"] = "trending"
        item["raw_text"] = card.get()
        item["is_desensitized"] = False
        return item

    @staticmethod
    def _parse_count(texts: list) -> int:
        """从文本列表中解析数字（如 '1,234' → 1234, '56 stars today' → 56）。"""
        import re
        for text in texts:
            # 匹配带逗号的数字
            match = re.search(r"([\d,]+)", text)
            if match:
                try:
                    return int(match.group(1).replace(",", ""))
                except ValueError:
                    continue
        return 0
