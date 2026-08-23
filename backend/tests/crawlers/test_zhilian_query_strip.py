"""zhilian spider 详情 URL 剥离追踪参数测试。

背景：智联 robots.txt 以 `Disallow: /*?*` 拒绝一切带 query 的请求
（scrapy ROBOTSTXT_OBEY + Protego 匹配 path+query），若详情 URL 保留
refcode/srccode/preactionid 追踪参数会被 robots 中间件直接丢弃，导致
详情页全部走 errback 降级为列表页摘要、正文（description/requirements）缺失。
"""

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_BACKEND / "data"))

from scrapy.http import HtmlResponse, Request

from crawlers.spiders import zhilian


def _response(url: str = "https://www.zhaopin.com/jobs?jl=530&kw=Python&pn=1"):
    """构造含一条岗位（positionUrl 带追踪参数）的新版 SSR 列表页响应。

    2026-08 改版：岗位数据在 __INITIAL_STATE__.positionList（DOM 卡片仅展示）。
    """
    body = """
    <html>
      <body>
        <div class="job-card">Python 工程师</div>
        <script>
        window.__INITIAL_STATE__={"positionList":[{"name":"Python 工程师","number":"CC123456","companyName":"测试公司","salary60":"20-30K","workingExp":"3-5年","education":"本科","workCity":"北京","cityDistrict":"海淀","jobSkillTags":[{"name":"Python"}],"positionUrl":"https://www.zhaopin.com/jobdetail/CC123456.htm?refcode=4019&srccode=401903&preactionid=abc","publishTime":"2026-08-23 01:09:29"}],"hasMore":false}
        </script>
      </body>
    </html>
    """
    req = Request(url=url, meta={"keyword": "Python", "city": "北京", "page": 1})
    return HtmlResponse(
        url=url, body=body.encode("utf-8"), encoding="utf-8", request=req
    )


def test_detail_request_strips_query_params():
    """详情请求 URL 必须剥离 refcode/srccode/preactionid 等追踪参数。

    若保留 query，scrapy ROBOTSTXT_OBEY 会按 robots.txt `Disallow: /*?*`
    判定 DENIED 并丢弃请求，正文永远回不来。
    """
    spider = zhilian.ZhilianSpider()
    resp = _response()
    requests = list(spider.parse(resp))

    detail_reqs = [r for r in requests if isinstance(r, Request) and not r.meta.get("playwright")]
    assert len(detail_reqs) == 1

    url = detail_reqs[0].url
    assert url == "https://www.zhaopin.com/jobdetail/CC123456.htm"
    assert "?" not in url


def test_detail_request_source_id_stays_correct():
    """剥离 query 不影响 source_id 提取（仍为 jobdetail 后的 number）。"""
    spider = zhilian.ZhilianSpider()
    resp = _response()
    requests = list(spider.parse(resp))

    detail_reqs = [r for r in requests if isinstance(r, Request) and not r.meta.get("playwright")]
    job = detail_reqs[0].meta["job"]
    assert job["source_id"] == "CC123456"
