"""icourse163 占位请求机制测试。

修复背景（08-23，226 LAN 部署实证）：占位请求此前打真实
search.htm，依赖外网/代理可达——worker 容器 HTTPS_PROXY 指向不可达代理时
DNS 失败进 errback，而 errback 只打日志不采集，整轮 0 产出且日志误导
（"开始调用采集脚本"实际未调用）。修复后：占位打本地不可达端口
（不依赖网络/代理/DNS，连接拒绝即刻进 errback），errback 转发 parse
真正执行采集（与 boss/maimai 的 errback→parse 模式一致）。
"""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from tests.helpers import FakeProc

_BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_BACKEND / "data"))

from crawlers.spiders.icourse163 import Icourse163Spider


def _make_spider():
    spider = Icourse163Spider.__new__(Icourse163Spider)
    spider.name = "icourse163"
    spider.platform = "icourse163"
    spider.keywords = []
    spider.max_pages = 3
    spider.crawler_script = "icourse163_crawler.py"
    spider.download_delay = 10
    return spider


class TestIcourse163Placeholder:
    def test_placeholder_targets_local_unreachable_port(self):
        """占位请求打本地不可达端口：不走外网/代理，且 dont_retry 跳过重试。"""
        spider = _make_spider()
        requests = list(spider.start_requests())

        assert len(requests) == 1
        req = requests[0]
        assert req.url.startswith("http://127.0.0.1:")
        assert req.meta.get("dont_retry") is True
        assert req.meta.get("keywords") == []
        assert req.errback == spider._on_error

    def test_errback_forwards_to_parse_and_collects(self, monkeypatch):
        """errback 转发 parse：占位失败后采集脚本仍被调用（不再死胡同 0 产出）。"""
        calls = []

        def fake_popen(cmd, **kwargs):
            calls.append(cmd)
            return FakeProc([])

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        spider = _make_spider()

        req = list(spider.start_requests())[0]
        fake_failure = SimpleNamespace(request=req)

        items = list(spider._on_error(fake_failure))

        assert items == []  # FakeProc 无产出，重点是脚本被真实调用
        assert len(calls) == 1
        assert calls[0][calls[0].index("--keyword") + 1] == ""
