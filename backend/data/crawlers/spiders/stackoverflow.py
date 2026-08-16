"""Stack Overflow 爬虫（技术热点观察池数据源）。

策略（2026-08 改造：原 Playwright 渲染标签页被 Cloudflare 拦截，改走官方 API）：
- 调用 Stack Exchange 官方 API（https://api.stackexchange.com/2.3/questions）
  - 按 tag 拉取最新问题，返回结构化 JSON（title/score/views/answers/tags）
  - 无需 API key，无 Cloudflare 拦截，官方配额 300 req/day（无 key）
- 项目 SO 为每日低频采集（每 tag 1 页），配额足够
- 产出 CommunityTrendItem（trend_type=newest）

合规：
- 遵循 Stack Exchange API 使用条款：https://api.stackexchange.com/docs
- 仅采集公开问题元数据（标题/标签/票数/浏览数），不下载正文

运行：
  scrapy crawl stackoverflow -a tags=python,machine-learning -a max_pages=1 -o output/stackoverflow.jsonl
  # 国际 API，需代理
  $env:HTTPS_PROXY="http://127.0.0.1:7890"
"""

import json
from datetime import datetime, timezone
from urllib.parse import quote

from scrapy import Request, Spider
from scrapy.exceptions import CloseSpider
from scrapy.http import Response

from crawlers.items import CommunityTrendItem
from crawlers.settings import RATE_LIMIT


# Stack Exchange API 端点（site=stackoverflow）
STACKEXCHANGE_API = "https://api.stackexchange.com/2.3/questions"

# 默认标签：空 = 全局热度（08-16 用户决策，不限定标签，按投票数排序）；
# 传 -a tags=python,machine-learning 时按标签分别采集
DEFAULT_TAGS: list[str] = []

# 单次请求条数上限（API 允许 100）
PAGE_SIZE = 100


class StackoverflowSpider(Spider):
    """Stack Overflow 标签页采集（Stack Exchange 官方 API）。"""

    name = "stackoverflow"
    platform = "stackoverflow"

    # 单次采集总上限（多标签/多页合计，08-16 用户决策）
    max_items_total = 100

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._collected = 0  # 单次采集累计产出（跨标签合计）
        # -a tags=python,machine-learning 覆盖默认标签
        tags = kwargs.get("tags")
        self.tags = tags.split(",") if tags else DEFAULT_TAGS
        # -a max_pages=3 控制单标签翻页数（API 的 page 参数）
        self.max_pages = int(kwargs.get("max_pages", "1"))
        # 请求间隔（API 官方限速宽松，沿用平台配置）
        limit = RATE_LIMIT.get(self.platform, {})
        delay_range = limit.get("delay_range", (10, 20))
        self.download_delay = sum(delay_range) / 2

    async def start(self):
        """Scrapy 2.13+ 入口：桥接到 start_requests。"""
        for request in self.start_requests():
            yield request

    def start_requests(self):
        # 空标签 = 全局热度（08-16 用户决策，sort=votes 无 tagged）
        tags = self.tags or [""]
        for tag in tags:
            self.logger.info(f"开始采集 Stack Overflow: {tag or '全局热度'} (API)")
            url = self._build_api_url(tag, 1)
            yield self._make_request(url, meta={"tag": tag, "page": 1})

    def parse(self, response: Response):
        """解析 Stack Exchange API 响应，产出 CommunityTrendItem。"""
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError as e:
            self.logger.error(f"API 响应解析失败: {e}")
            return

        if data.get("error_id"):
            self.logger.error(f"API 错误: {data.get('error_message')} ({data.get('error_name')})")
            return

        items = data.get("items", [])
        item_count = 0
        for it in items:
            if self._collected >= self.max_items_total:
                break
            item = self._api_item_to_item(it, response.meta)
            if item:
                item_count += 1
                self._collected += 1
                yield item

        # 翻页：has_more 且未达 max_pages
        current_page = response.meta.get("page", 1)
        self.logger.info(
            f"[stackoverflow] tag={response.meta['tag']} 页={current_page} 产出 {item_count} 条"
        )
        if self._collected >= self.max_items_total:
            raise CloseSpider(f"达到单次采集上限 {self.max_items_total} 条")
        if data.get("has_more") and current_page < self.max_pages:
            tag = response.meta["tag"]
            next_url = self._build_api_url(tag, current_page + 1)
            yield self._make_request(
                next_url,
                meta={"tag": tag, "page": current_page + 1},
            )

    def _build_api_url(self, tag: str, page: int) -> str:
        """构造 Stack Exchange 查询 URL（最新问题排序；tag 空 = 全局热度按投票）。"""
        if tag:
            return (
                f"{STACKEXCHANGE_API}?tagged={quote(tag)}&site=stackoverflow"
                f"&sort=creation&order=desc&pagesize={PAGE_SIZE}&page={page}"
            )
        # 全局热度（08-16 用户决策）：无标签过滤，按投票数排序取热门问题
        return (
            f"{STACKEXCHANGE_API}?site=stackoverflow"
            f"&sort=votes&order=desc&pagesize={PAGE_SIZE}&page={page}"
        )

    def _make_request(self, url: str, meta: dict):
        """构造 API 请求（普通 HTTP，无需 Playwright）。"""
        return Request(
            url,
            callback=self.parse,
            meta=meta,
            headers={
                # Stack Exchange 要求携带标识应用的 User-Agent
                "User-Agent": "zhigang-compass/1.0 (competition demo, non-commercial)",
                "Accept": "application/json",
            },
            dont_filter=True,
        )

    def _api_item_to_item(self, it: dict, meta: dict) -> CommunityTrendItem | None:
        """将 Stack Exchange API 问题对象转为 CommunityTrendItem。"""
        question_id = it.get("question_id")
        title = it.get("title", "")
        if not question_id or not title:
            return None

        # creation_date 为 unix 秒 → ISO8601
        asked_at = ""
        if it.get("creation_date"):
            asked_at = datetime.fromtimestamp(
                it["creation_date"], tz=timezone.utc
            ).isoformat()

        item = CommunityTrendItem()
        item["source"] = self.platform
        item["source_id"] = str(question_id)
        item["source_url"] = it.get("link", "")
        item["crawled_at"] = datetime.now(timezone.utc).isoformat()
        item["title"] = title
        item["description"] = ""  # API 摘要需额外字段，列表页足够
        item["url"] = it.get("link", "")
        item["tags"] = it.get("tags", []) or []
        item["votes"] = int(it.get("score", 0) or 0)
        item["views"] = int(it.get("view_count", 0) or 0)
        item["answers"] = int(it.get("answer_count", 0) or 0)
        item["asked_at"] = asked_at
        item["language"] = meta.get("tag", "")  # 用 tag 作为 language 字段复用
        item["trend_type"] = "newest"
        item["raw_text"] = json.dumps(it, ensure_ascii=False)
        item["is_desensitized"] = False
        return item
