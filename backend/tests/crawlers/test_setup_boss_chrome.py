"""setup_boss_chrome 平台独立浏览器配置测试。

每个 CDP 爬虫使用独立 profile + 独立端口（登录态/验证状态互不污染）。
"""

from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data"))

from crawlers.setup_boss_chrome import (
    BOSS_CHROME_PROFILE_DIR,
    CDP_PORT_BY_PLATFORM,
    platform_profile_dir,
)


def test_platform_profile_dir_isolated_per_platform():
    """各平台独立 profile 目录（boss 默认兼容，其余平台互不共享）。"""
    boss = platform_profile_dir("boss")
    monster = platform_profile_dir("monster")
    glassdoor = platform_profile_dir("glassdoor")
    maimai = platform_profile_dir("maimai")

    assert boss == BOSS_CHROME_PROFILE_DIR
    assert len({boss, monster, glassdoor, maimai}) == 4
    assert monster.name == "monster-chrome-profile"
    assert glassdoor.name == "glassdoor-chrome-profile"
    assert maimai.name == "maimai-chrome-profile"
    # 都在 ~/.zhigang-compass 隔离根目录下
    root = Path.home() / ".zhigang-compass"
    assert all(str(p).startswith(str(root)) for p in (boss, monster, glassdoor, maimai))


def test_cdp_port_by_platform_distinct():
    """各平台独立 CDP 端口（互不共享浏览器实例）。"""
    assert CDP_PORT_BY_PLATFORM == {
        "boss": 9222,
        "monster": 9223,
        "glassdoor": 9224,
        "maimai": 9225,
        "osta": 9226,
    }
    assert len(set(CDP_PORT_BY_PLATFORM.values())) == len(CDP_PORT_BY_PLATFORM)


def test_ensure_cdp_chrome_launches_with_endpoint_port(monkeypatch):
    """拉起 Chrome 的端口必须与 CDP 端点一致（回归：分端口后 start_chrome 仍用默认 9222）。"""
    from crawlers import setup_boss_chrome as sbc

    called: dict = {}

    def fake_check(cdp_url, quiet=False):
        return False

    def fake_start(cdp_port, cdp_address="127.0.0.1", url="", profile_dir=None):
        called["cdp_port"] = cdp_port
        called["profile_dir"] = profile_dir

    monkeypatch.setattr(sbc, "check_cdp", fake_check)
    monkeypatch.setattr(sbc, "start_chrome", fake_start)
    monkeypatch.setattr("time.sleep", lambda *a, **k: None)  # 跳过 20s 轮询

    sbc.ensure_cdp_chrome(
        "http://127.0.0.1:9223",
        wait_seconds=2,
        profile_dir=platform_profile_dir("monster"),
    )
    assert called["cdp_port"] == 9223
    assert called["profile_dir"] == platform_profile_dir("monster")
