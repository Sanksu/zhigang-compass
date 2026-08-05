"""爬虫基类：统一搜索关键字/城市配置 + 合规声明 + JobItem 构造。"""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from scrapy import Spider, Request
from scrapy.http import Response

from crawlers.settings import RATE_LIMIT, MAIMAI_COMPLIANCE
from crawlers.items import JobItem


# 默认搜索关键字（覆盖项目关注的 AI/大数据/全栈方向）
DEFAULT_KEYWORDS = [
    "Python", "Java", "前端", "算法工程师",
    "数据分析", "大模型", "全栈", "后端",
]

# 默认搜索城市
DEFAULT_CITIES = ["北京", "上海", "深圳", "杭州", "广州"]


class BaseSpider(Spider):
    """所有爬虫的共享基类。

    自动处理：
    - 速率限制（从 RATE_LIMIT 读取）
    - 搜索关键字/城市遍历（支持 -a keywords=Python,Java 覆盖）
    - 脉脉合规声明注入
    - JobItem 统一构造
    """

    platform: str = ""  # 子类必须设置：boss / zhilian / monster / ...

    # 子类可覆盖：搜索关键字与城市列表
    keywords: list[str] = DEFAULT_KEYWORDS
    cities: list[str] = DEFAULT_CITIES

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 平台级限速接线：从 RATE_LIMIT.delay_range 取中点设置
        # download_delay，与 arxiv/coursera 等非招聘源一致；避免招聘爬虫走
        # 全局默认 2s（折算 ~40 req/min）超出 settings.py 声明的 20 req/min 上限
        self.limit = RATE_LIMIT.get(self.platform, {})
        delay_range = self.limit.get("delay_range")
        if delay_range and len(delay_range) == 2:
            self.download_delay = sum(delay_range) / 2
        # 支持 -a keywords=Python,Java -a cities=北京,上海 运行时覆盖
        if kwargs.get("keywords"):
            self.keywords = kwargs["keywords"].split(",")
        if kwargs.get("cities"):
            self.cities = kwargs["cities"].split(",")

    def start_requests(self):
        raise NotImplementedError("子类必须实现 start_requests")

    async def start(self):
        """Scrapy 2.13+ 入口：桥接到子类的 start_requests。

        保留 start_requests 是为了让子类用同步 generator 写法更直观，
        且便于单元测试直接调用 start_requests()。
        """
        for request in self.start_requests():
            yield request

    def parse(self, response: Response):
        raise NotImplementedError("子类必须实现 parse")

    def _compliance_headers(self) -> dict:
        """脉脉合规声明头。"""
        if self.platform == "maimai":
            return {"X-Collection-Purpose": MAIMAI_COMPLIANCE["annotation"]}
        return {}

    def make_item(self, **fields) -> JobItem:
        """构造 JobItem，自动填充 source / crawled_at / 合规标记。"""
        item = JobItem()
        item["source"] = self.platform
        item["crawled_at"] = datetime.now(timezone(timedelta(hours=8))).isoformat()
        item["is_desensitized"] = False
        for k, v in fields.items():
            if k in item.fields:
                item[k] = v
        return item

    @staticmethod
    def build_query(params: dict) -> str:
        """构造 URL query string。"""
        return urlencode(params)
