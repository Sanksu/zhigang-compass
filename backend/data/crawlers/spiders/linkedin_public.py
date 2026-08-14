"""LinkedIn 爬虫（C 级 — 实验性，需走代理）。

策略（2026-07-29 重构）：
- 主链路：调用 JobSpy（speedyapply/JobSpy，2026-02 仍维护）采集
  - JobSpy 内部解析 LinkedIn JSON-LD 结构化数据，字段丰富稳定
  - JobSpy 是同步阻塞库，与 Scrapy Twisted 冲突 → 用 subprocess 调独立脚本
- 参考项目：https://github.com/speedyapply/JobSpy

⚠️ 合规提醒：
- 仅采集公开搜索页
- 需走 Clash/V2Ray 代理（HTTPS_PROXY 环境变量）
- LinkedIn 公开页在中国大陆 IP 无法访问，必须走代理

运行：
  # 先启动 Clash（7890 端口）
  $env:HTTPS_PROXY="http://127.0.0.1:7890"
  scrapy crawl linkedin_public -a keywords=Python -a cities="New York" -o output/linkedin_public.jsonl
"""

from crawlers.spiders.jobspy_base import JobSpyBaseSpider


class LinkedInPublicSpider(JobSpyBaseSpider):
    name = "linkedin_public"
    platform = "linkedin_public"
    site_name = "linkedin"

    # LinkedIn 默认搜索美国城市
    cities = ["New York", "San Francisco", "Seattle", "Boston", "Remote"]

    # 单次采集岗位数上限
    results_wanted = 100
