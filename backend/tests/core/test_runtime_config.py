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
        assert data["etl_batch_cap"] == 2000
        assert data["etl_structure_load_default"] == 500
        assert data["etl_validate_temporal_default"] == 200
        assert data["etl_run_hour"] == 5
        assert data["etl_run_minute"] == 0

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
            "etl_batch_cap": 3000,
            "etl_structure_load_default": 600,
            "etl_validate_temporal_default": 300,
            "etl_run_hour": 3,
            "etl_run_minute": 30,
        })
        assert data["arq_concurrency"] == 4
        assert data["crawl_items_cap"] == 200
        assert data["etl_batch_cap"] == 3000
        assert data["etl_run_hour"] == 3
        assert data["etl_run_minute"] == 30
        # 持久化落盘
        on_disk = json.loads(_isolated_config.read_text(encoding="utf-8"))
        assert on_disk["arq_job_timeout"] == 900
        assert on_disk["rate_limit"]["zhilian"]["delay_range"] == [5, 10]
        assert on_disk["etl_structure_load_default"] == 600
        # 缓存同步
        assert rc.get("crawl_items_cap") == 200
        assert rc.get("etl_validate_temporal_default") == 300

    def test_save_invalid_scalar_rejected(self, _isolated_config):
        with pytest.raises(ValueError):
            rc.save({"arq_concurrency": 0})
        with pytest.raises(ValueError):
            rc.save({"arq_job_timeout": "1800"})
        with pytest.raises(ValueError):
            rc.save({"alert_webhook_url": "ftp://bad"})
        with pytest.raises(ValueError):
            rc.save({"evolution_cache_ttl": 1})

    def test_save_invalid_etl_rejected(self, _isolated_config):
        with pytest.raises(ValueError):
            rc.save({"etl_batch_cap": 50})  # < 100
        with pytest.raises(ValueError):
            rc.save({"etl_structure_load_default": 5000})  # > 1000
        with pytest.raises(ValueError):
            rc.save({"etl_validate_temporal_default": "200"})
        with pytest.raises(ValueError):
            rc.save({"etl_run_hour": 24})
        with pytest.raises(ValueError):
            rc.save({"etl_run_minute": -1})

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


class TestSaveCrawlers:
    """每爬虫采集配置（08-21）：enabled/max_results 校验与规范化。"""

    def test_save_crawlers_valid(self, _isolated_config):
        data = rc.save({
            "crawlers": {
                "zhilian": {"enabled": False, "max_results": 150},
                "arxiv": {"max_results": 50},
                "github": {"enabled": True},
            },
        })
        crawlers = data["crawlers"]
        assert crawlers["zhilian"] == {"enabled": False, "max_results": 150}
        assert crawlers["arxiv"] == {"max_results": 50}
        assert crawlers["github"] == {"enabled": True}
        on_disk = json.loads(_isolated_config.read_text(encoding="utf-8"))
        assert on_disk["crawlers"]["zhilian"]["max_results"] == 150

    def test_save_crawlers_defaults_when_missing_file(self, _isolated_config):
        assert rc.load_all()["crawlers"] == {}

    def test_save_crawlers_invalid_rejected(self, _isolated_config):
        with pytest.raises(ValueError):
            rc.save({"crawlers": {"zhilian": {"enabled": "yes"}}})
        with pytest.raises(ValueError):
            rc.save({"crawlers": {"zhilian": {"max_results": 5}}})  # < 10
        with pytest.raises(ValueError):
            rc.save({"crawlers": {"zhilian": {"max_results": 2000}}})  # > 1000
        with pytest.raises(ValueError):
            rc.save({"crawlers": {"zhilian": "not-object"}})
        with pytest.raises(ValueError):
            rc.save({"crawlers": []})

    def test_save_crawlers_full_replace(self, _isolated_config):
        """整体覆盖语义（与 rate_limit 一致）：再次 save 会替换整个 crawlers 对象。"""
        rc.save({"crawlers": {"zhilian": {"enabled": False}}})
        data = rc.save({"crawlers": {"arxiv": {"max_results": 80}}})
        assert "zhilian" not in data["crawlers"]  # 整体替换，非增量
        assert data["crawlers"]["arxiv"]["max_results"] == 80

    def test_save_crawlers_empty_resets(self, _isolated_config):
        """清空 crawlers：提交空对象即全部恢复默认（全启用）。"""
        rc.save({"crawlers": {"zhilian": {"enabled": False}}})
        data = rc.save({"crawlers": {}})
        assert data["crawlers"] == {}
