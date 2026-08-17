"""运行时配置模块单元测试（08-16：管理后台可编辑、重启生效）。

覆盖：文件缺失/损坏回退默认、save 校验与持久化、rate_limit 结构校验、
get/load_all 语义。
"""

import json

import pytest

from app.core import runtime_config as rc


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """每个用例使用独立配置文件并重置缓存。"""
    cfg_file = tmp_path / "runtime_settings.json"
    monkeypatch.setattr(rc, "_RUNTIME_CONFIG_PATH", cfg_file)
    monkeypatch.setattr(rc, "_cache", None)
    return cfg_file


class TestLoad:
    def test_missing_file_returns_defaults(self):
        data = rc.load_all()
        assert data["arq_concurrency"] == 10
        assert data["arq_job_timeout"] == 1800
        assert data["alert_webhook_url"] == ""
        assert data["evolution_cache_ttl"] == 60
        assert data["crawl_items_cap"] == 100
        assert data["rate_limit"] == {}

    def test_corrupt_file_returns_defaults(self, _isolated_config):
        _isolated_config.write_text("{not-json", encoding="utf-8")
        assert rc.load_all()["crawl_items_cap"] == 100

    def test_partial_file_merges_defaults(self, _isolated_config):
        _isolated_config.write_text(json.dumps({"arq_concurrency": 5}), encoding="utf-8")
        data = rc.load_all()
        assert data["arq_concurrency"] == 5
        assert data["arq_job_timeout"] == 1800  # 未配置项回退默认


class TestSave:
    def test_save_persists_and_validates(self, _isolated_config):
        data = rc.save({
            "arq_concurrency": 4,
            "arq_job_timeout": 900,
            "alert_webhook_url": "https://example.com/hook",
            "evolution_cache_ttl": 120,
            "crawl_items_cap": 200,
            "rate_limit": {"zhilian": {"req_per_min": 3, "delay_range": [5, 10]}},
        })
        assert data["arq_concurrency"] == 4
        assert data["crawl_items_cap"] == 200
        # 持久化落盘
        on_disk = json.loads(_isolated_config.read_text(encoding="utf-8"))
        assert on_disk["arq_job_timeout"] == 900
        assert on_disk["rate_limit"]["zhilian"]["delay_range"] == [5, 10]
        # 缓存同步
        assert rc.get("crawl_items_cap") == 200

    def test_save_invalid_scalar_rejected(self, _isolated_config):
        with pytest.raises(ValueError):
            rc.save({"arq_concurrency": 0})
        with pytest.raises(ValueError):
            rc.save({"arq_job_timeout": "1800"})
        with pytest.raises(ValueError):
            rc.save({"alert_webhook_url": "ftp://bad"})
        with pytest.raises(ValueError):
            rc.save({"evolution_cache_ttl": 1})

    def test_save_invalid_rate_limit_rejected(self, _isolated_config):
        with pytest.raises(ValueError):
            rc.save({"rate_limit": {"zhilian": {"delay_range": [20, 5]}}})  # min > max
        with pytest.raises(ValueError):
            rc.save({"rate_limit": {"zhilian": {"req_per_min": 0}}})
        with pytest.raises(ValueError):
            rc.save({"rate_limit": {"zhilian": {"delay_range": [1, 2, 3]}}})

    def test_save_returns_normalized(self, _isolated_config):
        data = rc.save({"rate_limit": {"maimai": {"delay_range": (8, 12)}}})
        assert data["rate_limit"]["maimai"]["delay_range"] == [8, 12]

    def test_partial_save_keeps_existing(self, _isolated_config):
        """拆页语义：只提交部分键时，未提供的键保留文件现有值。"""
        rc.save({"arq_concurrency": 15, "crawl_items_cap": 200})
        data = rc.save({"arq_concurrency": 8})
        assert data["arq_concurrency"] == 8
        assert data["crawl_items_cap"] == 200  # 未被覆盖
        assert data["arq_job_timeout"] == 1800
