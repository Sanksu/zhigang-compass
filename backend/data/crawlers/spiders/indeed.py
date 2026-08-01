"""Indeed 爬虫（B 级 — 补充源，需走代理）。

策略（2026-07-29 重构）：
- 主链路：调用 JobSpy（speedyapply/JobSpy，2026-02 仍维护）采集
  - JobSpy 内部解析 Indeed JSON-LD 结构化数据，字段丰富稳定
  - JobSpy 是同步阻塞库，与 Scrapy Twisted 冲突 → 用 subprocess 调独立脚本
- 参考项目：https://github.com/speedyapply/JobSpy

⚠️ 合规提醒：
- 仅采集公开搜索页
- 需走 Clash/V2Ray 代理（HTTPS_PROXY 环境变量）
- Indeed 官方称"无速率限制"，但仍保守取数

运行：
  # 先启动 Clash（7890 端口）
  $env:HTTPS_PROXY="http://127.0.0.1:7890"
  scrapy crawl indeed -a keywords=Python -a cities="New York" -o output/indeed.jsonl
"""

from crawlers.spiders.jobspy_base import JobSpyBaseSpider


class IndeedSpider(JobSpyBaseSpider):
    name = "indeed"
    platform = "indeed"
    site_name = "indeed"

    # Indeed 默认搜索美国城市
    cities = ["New York", "San Francisco", "Seattle", "Boston", "Remote"]

    # 单次采集岗位数上限（保守值，Indeed 无限流但避免压力）
    results_wanted = 20
