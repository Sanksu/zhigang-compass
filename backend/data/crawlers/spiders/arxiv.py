"""arXiv 论文爬虫（技术热点观察池数据源）。

策略：
- 调用 arXiv 官方 API（https://export.arxiv.org/api/query），返回 Atom XML
- 按 cs.* 分类拉取最新论文，每日采集
- 无需认证，官方限速 1 req/3s（RATE_LIMIT 已配置 3-5s 间隔）
- 产出 PaperItem，用于「技术热点观察池」，不独立触发 candidate

合规：
- 仅采集公开元数据（标题/摘要/作者/分类），不下载 PDF 内容
- 遵循 arXiv API 使用条款：https://info.arxiv.org/help/api/tou.html

运行：
  scrapy crawl arxiv -a categories=cs.AI,cs.LG -a max_results=50 -o output/arxiv.jsonl
  # 国际源，需代理
  $env:HTTPS_PROXY="http://127.0.0.1:7890"
"""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from scrapy import Request, Spider
from scrapy.exceptions import CloseSpider
from scrapy.http import Response

from crawlers.items import PaperItem
from crawlers.settings import RATE_LIMIT


# arXiv API 端点
ARXIV_API = "https://export.arxiv.org/api/query"

# 默认分类：空 = 全局最新（08-16 用户决策，不限定 cs.* 分类）；
# 传 -a categories=cs.AI,cs.LG 时按分类分别拉取
DEFAULT_CATEGORIES: list[str] = []


class ArxivSpider(Spider):
    """arXiv 论文采集：官方 API + Atom XML 解析。

    不继承 BaseSpider（BaseSpider.make_item 是 JobItem 专属），
    但复用 RATE_LIMIT 配置与 keywords/cities 参数风格。
    """

    name = "arxiv"
    platform = "arxiv"

    # 单次采集总上限（多分类合计，08-16 用户决策）
    max_items_total = 100

    # Atom 命名空间
    namespaces = {"atom": "http://www.w3.org/2005/Atom"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._collected = 0  # 单次采集累计产出（跨分类合计）
        # -a categories=cs.AI,cs.LG 覆盖默认分类
        cats = kwargs.get("categories")
        self.categories = cats.split(",") if cats else DEFAULT_CATEGORIES
        # -a max_results=50 控制单分类拉取数
        self.max_results = int(kwargs.get("max_results", "100"))
        # arXiv 官方约束 1 req/3s，通过 download_delay 控制
        limit = RATE_LIMIT.get(self.platform, {})
        delay_range = limit.get("delay_range", (3, 5))
        self.download_delay = sum(delay_range) / 2

    async def start(self):
        """Scrapy 2.13+ 入口：桥接到 start_requests。"""
        for request in self.start_requests():
            yield request

    def start_requests(self):
        # API 例外（08-14 用户确认 B 方案）：export.arxiv.org 官方 API 条款明确
        # 允许程序化访问，robots.txt 保守 Disallow: /——跳过 robots 检查
        # （合规依据：https://info.arxiv.org/help/api/）
        if self.categories:
            for cat in self.categories:
                params = {
                    "search_query": f"cat:{cat}",
                    "start": 0,
                    "max_results": self.max_results,
                    "sortBy": "submittedDate",
                    "sortOrder": "descending",
                }
                url = f"{ARXIV_API}?{urlencode(params)}"
                self.logger.info(f"开始采集 arXiv 分类 {cat}（max={self.max_results}）")
                yield Request(
                    url,
                    callback=self.parse,
                    meta={"category": cat, "dont_obey_robotstxt": True},
                    headers={"User-Agent": "zhigang-compass/1.0 (academic-research)"},
                )
            return

        # 全局最新（08-16 用户决策）：cat:* 匹配全部分类（API 不接受省略
        # search_query，实测 cat:* 通配返回全站最新投稿）
        params = {
            "search_query": "cat:*",
            "start": 0,
            "max_results": self.max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        url = f"{ARXIV_API}?{urlencode(params)}"
        self.logger.info(f"开始采集 arXiv 全局最新（max={self.max_results}）")
        yield Request(
            url,
            callback=self.parse,
            meta={"category": "global", "dont_obey_robotstxt": True},
            headers={"User-Agent": "zhigang-compass/1.0 (academic-research)"},
        )

    def parse(self, response: Response):
        """解析 Atom XML，产出 PaperItem。"""
        # 注册命名空间后用 xpath
        entries = response.selector.root.findall("atom:entry", self.namespaces)

        if not entries:
            self.logger.warning(
                f"分类 {response.meta['category']} 未解析到 entry，检查 API 响应"
            )
            # 保存原始响应便于排查
            self.logger.debug(f"响应前 500 字符: {response.text[:500]}")
            return

        item_count = 0
        for entry in entries:
            if self._collected >= self.max_items_total:
                break
            item = self._entry_to_item(entry, response.meta["category"])
            if item:
                item_count += 1
                self._collected += 1
                yield item

        self.logger.info(f"[arxiv] 分类 {response.meta['category']} 产出 {item_count} 条论文")
        if self._collected >= self.max_items_total:
            raise CloseSpider(f"达到单次采集上限 {self.max_items_total} 条")

    def _entry_to_item(self, entry, category: str) -> PaperItem:
        """将 Atom <entry> 元素转为 PaperItem。"""
        from xml.etree import ElementTree as ET

        ns = self.namespaces["atom"]

        # arXiv ID：从 <id> 提取（如 http://arxiv.org/abs/2401.12345v1）
        id_elem = entry.find(f"{{{ns}}}id")
        if id_elem is None:
            return None
        id_text = id_elem.text or ""
        # 提取 2401.12345v1 部分
        arxiv_id = id_text.rstrip("/").split("/abs/")[-1] if "/abs/" in id_text else id_text
        source_url = id_text.strip()

        # 标题
        title_elem = entry.find(f"{{{ns}}}title")
        title = (title_elem.text or "").strip().replace("\n", " ") if title_elem is not None else ""

        # 摘要
        summary_elem = entry.find(f"{{{ns}}}summary")
        abstract = (summary_elem.text or "").strip() if summary_elem is not None else ""

        # 作者列表
        authors = []
        for author in entry.findall(f"{{{ns}}}author"):
            name_elem = author.find(f"{{{ns}}}name")
            if name_elem is not None and name_elem.text:
                authors.append(name_elem.text.strip())

        # 发布/更新时间
        published_elem = entry.find(f"{{{ns}}}published")
        updated_elem = entry.find(f"{{{ns}}}updated")
        published = (published_elem.text or "").strip() if published_elem is not None else ""
        updated = (updated_elem.text or "").strip() if updated_elem is not None else ""

        # 所有分类（<category term="...">）
        categories = []
        for cat in entry.findall(f"{{{ns}}}category"):
            term = cat.get("term", "")
            if term:
                categories.append(term)

        # PDF 链接（<link rel="related" type="application/pdf">）
        pdf_url = ""
        for link in entry.findall(f"{{{ns}}}link"):
            if link.get("type") == "application/pdf":
                pdf_url = link.get("href", "")
                break

        item = PaperItem()
        item["source"] = self.platform
        item["source_id"] = arxiv_id
        item["source_url"] = source_url
        item["crawled_at"] = datetime.now(timezone(timedelta(hours=8))).isoformat()
        item["title"] = title
        item["authors"] = authors
        item["abstract"] = abstract
        item["categories"] = categories or [category]
        item["published"] = published
        item["updated"] = updated
        item["pdf_url"] = pdf_url
        item["raw_text"] = ET.tostring(entry, encoding="unicode")
        item["is_desensitized"] = False
        return item
