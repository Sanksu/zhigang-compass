"""GitHub Trending 爬虫（技术热点观察池数据源）。

策略：
- 爬取 https://github.com/trending/{language}?since={daily|weekly|monthly} 公开页
- 解析 SSR HTML 中的仓库卡片（article.Box-row）
- 无需 token，无需 API 调用
- 产出 CommunityTrendItem（trend_type=trending）

合规：
- 仅采集公开 trending 页面元数据（仓库名/描述/star/fork/language）
- 请求间隔 6-12s，避免触发 GitHub 反爬

运行：
  scrapy crawl github -a languages=python,java,javascript -a since=daily -o output/github.jsonl
  # 国际源，需代理
  $env:HTTPS_PROXY="http://127.0.0.1:7890"
"""

from datetime import datetime, timedelta, timezone

from scrapy import Request, Spider
from scrapy.http import Response

from crawlers.items import CommunityTrendItem
from crawlers.settings import RATE_LIMIT


GITHUB_TRENDING_URL = "https://github.com/trending/{language}?since={since}"

# 默认关注的技术方向（与项目 AI/大数据/全栈方向一致）
DEFAULT_LANGUAGES = ["python", "java", "javascript", "typescript", "go"]

# 默认时间窗口（daily/weekly/monthly）
DEFAULT_SINCE = "daily"


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
        delay_range = limit.get("delay_range", (6, 12))
        self.download_delay = sum(delay_range) / 2

    async def start(self):
        """Scrapy 2.13+ 入口：桥接到 start_requests。"""
        for request in self.start_requests():
            yield request

    def start_requests(self):
        for lang in self.languages:
            url = GITHUB_TRENDING_URL.format(language=lang, since=self.since)
            self.logger.info(f"开始采集 GitHub Trending: {lang} ({self.since})")
            yield Request(
                url,
                callback=self.parse,
                meta={"language": lang, "since": self.since},
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )

    def parse(self, response: Response):
        """解析 GitHub Trending 页面，产出 CommunityTrendItem。"""
        # 每个仓库是 article.Box-row
        cards = response.css("article.Box-row")

        if not cards:
            # GitHub 可能改版，选择器需要对照真实页面验证
            self.logger.warning(
                f"未解析到仓库卡片（language={response.meta['language']}），"
                f"页面标题: {response.css('title::text').get(default='')}"
            )
            # 保存原始 HTML 便于排查选择器
            self.logger.debug(f"页面前 1000 字符: {response.text[:1000]}")
            return

        for card in cards:
            item = self._card_to_item(card, response.meta)
            if item:
                yield item

        self.logger.info(
            f"GitHub Trending [{response.meta['language']}/{response.meta['since']}] "
            f"采集 {len(cards)} 个仓库"
        )

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
