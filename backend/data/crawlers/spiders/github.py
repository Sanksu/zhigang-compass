"""GitHub Trending 爬虫（技术热点观察池数据源）。

策略：
- 爬取 GitHub Search API（官方 API 条款允许程序化访问；trending 网页 robots
  Disallow: / 且无官方 API——用 search/repositories created:>窗口 sort=stars 近似）
- 默认全局热度模式（08-16 用户决策）：不限语言，取窗口内 star 最高的一批
  仓库；可通过 -a languages= 覆盖为按语言过滤（每语言 20 个）
- 产出 CommunityTrendItem（trend_type=trending）

数据质量（08-31 修复）：
- 过滤刷票/作弊/破解/盗版垃圾仓库：查询层加 stars 下限 + 逐条关键词过滤
  （游戏外挂/雷达/脚本、破解软件、刷票营销话术），保证结果贴近最新技术发展
- 补抓仓库 README 正文写入 snapshot.readme，解决"无正文"问题（有界限流）

合规：
- 仅采集 API 返回的仓库元数据（full_name/描述/star/fork/language）与 README
- 无 token 限 10 req/min（全局模式 1 请求 / 语言模式 5 请求，请求间隔保留）
- 可选 GITHUB_TOKEN 提升配额并消除按 IP 二次限制（env 配置，缺省匿名）

运行：
  scrapy crawl github -a since=daily -o output/github.jsonl
  # 国际源，需代理
  $env:HTTPS_PROXY="http://127.0.0.1:7890"
"""

from datetime import datetime, timedelta, timezone

from scrapy import Request, Spider
from scrapy.http import Response

import json
import os
import re
from urllib.parse import quote

from crawlers.items import CommunityTrendItem
from crawlers.settings import CRAWL_ITEMS_CAP, RATE_LIMIT


GITHUB_SEARCH_API = "https://api.github.com/search/repositories"

# 默认时间窗口（daily/weekly/monthly）
DEFAULT_SINCE = "daily"

# 默认语言过滤：空 = 全局热度（不限语言，窗口内 star 最高 top 100）。
# 传 -a languages=python,java 时按语言分别取热度（每语言 20，合计 ≤100）
DEFAULT_LANGUAGES: list[str] = []


# ── 数据质量过滤阈值（08-31 修复：刷票/作弊/破解垃圾仓库治理）──
# 实测：新建窗口内被 starbomb 刷票的仓库 star 恒为个位数（≤10），描述常为
# "Download XXX — free, working" 营销话术，无真实技术正文，与最新技术发展无关。
MIN_STARS = 20           # 最低 star 数（查询层 q=stars:>=N + 逐条兜底），剔除刷票仓库
REQUIRE_LANGUAGE = True  # 要求主语言非空（空语言多为一键生成/占位仓库）
REQUIRE_DESC = True      # 要求描述非空（无正文不构成"技术热点"信号）

# 作弊/破解/外挂/盗版主题词：full_name 或 description 命中任一即丢弃。
# 覆盖游戏外挂/脚本/雷达/刷票、破解软件、盗版资源等与"最新技术发展"无关的仓库。
# 刻意剔除易误伤的词：radar/esp/god-mode（如 ai-tools-radar、ESP32 项目、godmode
# 配置仓库）——这类游戏作弊仓库名几乎都同时含 hack/cheat/crack/trainer，靠强词即可命中。
SPAM_KEYWORD_RE = re.compile(
    r"(hack|crack|cheat|cheat[- ]?table|trainer|aimbot|aim[- ]?bot|aimlock|"
    r"wallhack|wall[- ]?hack|instalock|install[- ]?lock|spoofer|executor|macro|"
    r"camo[- ]?unlock|auto[- ]?headshot|auto[- ]?aim|speed[- ]?hack|recoil[- ]?table|"
    r"pre[- ]?load[- ]?bypass|script[- ]?hub|auto[- ]?farm|item[- ]?spawner|"
    r"ranked[- ]?exploit|free[- ]?desktop[- ]?crack|night[- ]?vision[- ]?hack|"
    r"infinite[- ]?money|money[- ]?drop|money[- ]?glitch|v[- ]?bucks|xp[- ]?glitch)",
    re.IGNORECASE,
)
# 刷票营销描述（如 "Download XXX — free, working"）命中即丢弃
SPAM_DESC_RE = re.compile(r"\bdownload\b.*\b(free|working)\b", re.IGNORECASE)


# ── README 正文补抓（08-31 修复：解决"无正文"）──
FETCH_README = True       # 默认补抓仓库 README 正文（-a fetch_readme=0 可关闭）
README_ENRICH_LIMIT = 30  # 单次采集最多补抓的仓库数（有界，控配额/时延）
README_MAX_CHARS = 8000   # README 截断长度，避免超大正文撑爆 JSONB


def _is_spam_repo(full_name: str, description: str, topics: list) -> bool:
    """作弊/破解/外挂/刷票仓库判定：full_name + description + topics 命中即 True。"""
    haystack = f"{full_name} {description} {' '.join(topics)}"
    if SPAM_KEYWORD_RE.search(haystack):
        return True
    return bool(SPAM_DESC_RE.search(description))


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
        # -a fetch_readme=0 关闭 README 正文补抓
        self.fetch_readme = kwargs.get("fetch_readme", "1") != "0"
        self._readme_fetched = 0  # 本次采集已补抓 README 的仓库数
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
            # 全局热度：不限语言，窗口内 star 最高的仓库（单次采集上限内）。
            # stars:>=MIN_STARS 剔除 starbomb 刷票仓库（实测刷票仓库 ≤10 星）
            query = f"created:>{created} stars:>={MIN_STARS}"
            url = f"{GITHUB_SEARCH_API}?q={quote(query)}&sort=stars&order=desc&per_page={min(CRAWL_ITEMS_CAP, 100)}"
            self.logger.info(f"开始采集 GitHub 全局热门 (created>{created} stars>={MIN_STARS})")
            yield Request(
                url,
                callback=self.parse,
                meta={"language": "", "since": self.since, "dont_obey_robotstxt": True},
                headers=self._api_headers(),
            )
            return

        for lang in self.languages:
            query = f"language:{lang} created:>{created} stars:>={MIN_STARS}"
            url = f"{GITHUB_SEARCH_API}?q={quote(query)}&sort=stars&order=desc&per_page=20"
            self.logger.info(f"开始采集 GitHub 热门: {lang} (created>{created})")
            yield Request(
                url,
                callback=self.parse,
                # API 例外（08-14 用户确认 B 方案）：api.github.com 官方 API 条款
                # 允许合理程序化访问（robots 保守规则不适用 API 端点）
                meta={"language": lang, "since": self.since, "dont_obey_robotstxt": True},
                headers=self._api_headers(),
            )

    def _api_headers(self) -> dict:
        """GitHub Search API 请求头（可选 GITHUB_TOKEN 提升限流配额）。

        匿名无 token 时按 IP 10 req/min（Search 二次限制 10/min）；共享出口
        IP 下易触发 403 限流。环境变量 GITHUB_TOKEN 存在时附 Authorization
        提升到 30/min 并消除二次限制；未配置时维持匿名（不阻塞采集）。
        采集端设置：宿主机/容器 env 设 GITHUB_TOKEN 即可（compose 已透传）。
        """
        headers = {
            "User-Agent": "zhigang-compass/1.0 (github-api)",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

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
            if item is None:
                continue
            if not self.fetch_readme or self._readme_fetched >= README_ENRICH_LIMIT:
                # 关闭补抓或已达补抓上限：直接产出（无正文的仓库仅作元数据信号）
                yield item
                continue
            self._readme_fetched += 1
            full_name = item["source_id"]
            readme_url = f"https://api.github.com/repos/{full_name}/readme"
            yield Request(
                readme_url,
                callback=self.parse_readme,
                errback=self.readme_errback,
                meta={"item": item, "dont_obey_robotstxt": True},
                headers={**self._api_headers(), "Accept": "application/vnd.github.raw+json"},
                dont_filter=True,
            )
        self.logger.info(
            f"GitHub API [{response.meta['language']}/{response.meta['since']}] "
            f"采集 {len(items)} 个仓库"
        )

    def parse_readme(self, response: Response):
        """补抓仓库 README 正文并写入 item，然后产出该仓库。

        未取到正文时叠加元数据产出（fail-soft，不因单仓补抓失败丢整仓）。
        """
        item = response.meta["item"]
        text = (response.text or "").strip()
        if text:
            item["readme"] = text[:README_MAX_CHARS]
        return item

    def readme_errback(self, failure):
        """README 补抓失败（限流/404/超时）：保留元数据行，不阻塞采集。"""
        self.logger.warning(
            f"GitHub README 补抓失败: {failure.request.meta['item']['source_id']}: {failure.value}"
        )
        return failure.request.meta["item"]

    def _api_to_item(self, repo: dict, meta: dict) -> CommunityTrendItem:
        """将 GitHub Search API 仓库对象转为 CommunityTrendItem。

        数据质量过滤（08-31）：
        - 作弊/破解/外挂/刷票垃圾仓库（_is_spam_repo）→ None
        - star < MIN_STARS → None（查询层已加 stars:>=N，此处兜底）
        - 主语言为空（REQUIRE_LANGUAGE）/ 描述为空（REQUIRE_DESC）→ None
        """
        full_name = repo.get("full_name", "")
        if not full_name:
            return None
        description = (repo.get("description") or "").strip()
        language = repo.get("language") or meta.get("language", "")
        if _is_spam_repo(full_name, description, repo.get("topics") or []):
            self.logger.info(f"GitHub 过滤垃圾仓库: {full_name}")
            return None
        if int(repo.get("stargazers_count") or 0) < MIN_STARS:
            return None
        if REQUIRE_LANGUAGE and not language:
            return None
        if REQUIRE_DESC and not description:
            return None
        item = CommunityTrendItem()
        item["source"] = self.platform
        item["source_id"] = full_name  # owner/repo 作为唯一 ID
        item["source_url"] = repo.get("html_url", f"https://github.com/{full_name}")
        item["crawled_at"] = datetime.now(timezone(timedelta(hours=8))).isoformat()
        item["title"] = full_name
        item["description"] = description
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
