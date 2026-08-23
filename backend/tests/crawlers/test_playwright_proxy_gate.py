"""Playwright 代理按源门控测试。

修复背景（08-23）：此前 scrapy_settings 全局读 HTTPS_PROXY 注入
PLAYWRIGHT_LAUNCH_OPTIONS["proxy"]，国内 Playwright 源（zhilian 智联每日
主源）也硬依赖代理可达——Linux Docker 不解析 host.docker.internal 时智联
全灭。门控后：全局启动参数恒无代理（国内源直连），国际源 coursera 经
custom_settings 调 playwright_launch_options() 按 env 注入。
"""

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_BACKEND / "data"))

from crawlers.scrapy_settings import PLAYWRIGHT_LAUNCH_OPTIONS, playwright_launch_options
from crawlers.spiders.coursera import CourseraSpider
from crawlers.spiders.zhilian import ZhilianSpider


class TestPlaywrightLaunchOptionsHelper:
    def test_no_env_no_proxy(self):
        """env 无代理变量：仅 headless，无 proxy 键（国内源直连形态）。"""
        options = playwright_launch_options({})
        assert options == {"headless": True}

    def test_empty_env_treated_as_unset(self):
        """代理变量置空 = 关闭（LAN 无代理部署）：不注入 proxy。"""
        options = playwright_launch_options({"HTTPS_PROXY": "", "HTTP_PROXY": ""})
        assert "proxy" not in options

    def test_https_proxy_injected(self):
        options = playwright_launch_options({"HTTPS_PROXY": "http://clash:7890"})
        assert options["proxy"] == {"server": "http://clash:7890"}

    def test_http_proxy_fallback(self):
        options = playwright_launch_options({"HTTP_PROXY": "http://gw:8080"})
        assert options["proxy"] == {"server": "http://gw:8080"}


class TestProxyGatePerSpider:
    def test_global_launch_options_never_carry_proxy(self):
        """全局启动参数恒无 proxy：国内 Playwright 源（zhilian）继承直连。"""
        assert PLAYWRIGHT_LAUNCH_OPTIONS == {"headless": True}

    def test_zhilian_has_no_proxy_launch_override(self):
        """zhilian 无 PLAYWRIGHT_LAUNCH_OPTIONS 覆盖（继承全局直连形态）。"""
        override = getattr(ZhilianSpider, "custom_settings", {}) or {}
        assert "PLAYWRIGHT_LAUNCH_OPTIONS" not in override

    def test_coursera_launch_options_follow_env(self):
        """coursera 的启动参数与 helper 同 env 求值一致（国际源注入代理）。"""
        assert (
            CourseraSpider.custom_settings["PLAYWRIGHT_LAUNCH_OPTIONS"]
            == playwright_launch_options()
        )
