"""匹配权重加载回退测试。"""

from app.services.matching import weights
from app.services.matching.weights import (
    DOMAIN_BLOCKLIST_DEFAULT,
    DEFAULT_WEIGHTS,
    domain_blocklist_pair,
    load_domain_sem_blocklist,
    load_sim_threshold,
    load_weights,
)


def _write_config(tmp_path, monkeypatch, content: str) -> None:
    cfg = tmp_path / "match_weights.json"
    cfg.write_text(content, encoding="utf-8")
    monkeypatch.setattr("app.services.matching.weights._CONFIG_PATH", cfg)


def _write_blocklist(tmp_path, monkeypatch, content: str) -> None:
    cfg = tmp_path / "domain_sem_blocklist.json"
    cfg.write_text(content, encoding="utf-8")
    monkeypatch.setattr("app.services.matching.weights._BLOCKLIST_PATH", cfg)


class TestLoadWeights:
    def test_config_missing_falls_back_to_default(self, tmp_path, monkeypatch):
        """配置文件缺失时回退默认权重。"""
        monkeypatch.setattr(
            "app.services.matching.weights._CONFIG_PATH",
            tmp_path / "not_exist.json",
        )
        assert load_weights() == DEFAULT_WEIGHTS

    def test_config_corrupted_falls_back_to_default(self, tmp_path, monkeypatch):
        """配置损坏（非法 JSON/非数值）时回退默认权重。"""
        _write_config(tmp_path, monkeypatch, "{invalid json")
        assert load_weights() == DEFAULT_WEIGHTS

        _write_config(tmp_path, monkeypatch, '{"w_must": "abc"}')
        assert load_weights() == DEFAULT_WEIGHTS

    def test_all_zero_weights_falls_back_to_default(self, tmp_path, monkeypatch):
        """权重全 0 时回退默认权重，防止匹配总分恒为 0。"""
        _write_config(tmp_path, monkeypatch, '{"w_must": 0, "w_nice": 0, "w_exp": 0}')
        assert load_weights() == DEFAULT_WEIGHTS

    def test_negative_weights_falls_back_to_default(self, tmp_path, monkeypatch):
        """出现负权重时回退默认权重。"""
        _write_config(tmp_path, monkeypatch, '{"w_must": -0.1, "w_nice": 0.2, "w_exp": 0.2}')
        assert load_weights() == DEFAULT_WEIGHTS

    def test_valid_config_loads(self, tmp_path, monkeypatch):
        """合法配置正常加载。"""
        _write_config(tmp_path, monkeypatch, '{"w_must": 0.7, "w_nice": 0.1, "w_exp": 0.2}')
        assert load_weights() == (0.7, 0.1, 0.2)

    def test_partial_config_fills_defaults(self, tmp_path, monkeypatch):
        """配置缺键时按默认值补齐（补齐后 Σw 须为 1，否则按下一用例拒绝）。"""
        _write_config(tmp_path, monkeypatch, '{"w_must": 0.5, "w_nice": 0.3}')
        assert load_weights() == (0.5, 0.3, DEFAULT_WEIGHTS[2])

    def test_sum_above_one_falls_back_to_default(self, tmp_path, monkeypatch):
        """P2-11：Σw>1（击穿 total_score Field(le=1.0)）→ 拒绝回退默认权重。"""
        _write_config(tmp_path, monkeypatch, '{"w_must": 0.7, "w_nice": 0.2, "w_exp": 0.3}')
        assert load_weights() == DEFAULT_WEIGHTS

    def test_sum_below_one_falls_back_to_default(self, tmp_path, monkeypatch):
        """P2-11：Σw<1 同样拒绝（总分系统性偏低属口径错误，不做归一化）。"""
        _write_config(tmp_path, monkeypatch, '{"w_must": 0.5, "w_nice": 0.2, "w_exp": 0.2}')
        assert load_weights() == DEFAULT_WEIGHTS


class TestLoadSimThreshold:
    def test_config_missing_uses_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "app.services.matching.weights._CONFIG_PATH",
            tmp_path / "not_exist.json",
        )
        assert load_sim_threshold() == weights.SIM_THRESHOLD_DEFAULT

    def test_invalid_value_uses_default(self, tmp_path, monkeypatch):
        _write_config(tmp_path, monkeypatch, '{"sim_threshold": "abc"}')
        assert load_sim_threshold() == weights.SIM_THRESHOLD_DEFAULT

    def test_valid_value_loads(self, tmp_path, monkeypatch):
        _write_config(tmp_path, monkeypatch, '{"sim_threshold": 0.9}')
        assert load_sim_threshold() == 0.9


class TestLoadDomainBlocklist:
    def test_missing_falls_back_to_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "app.services.matching.weights._BLOCKLIST_PATH",
            tmp_path / "not_exist.json",
        )
        assert load_domain_sem_blocklist() == frozenset(frozenset(p) for p in DOMAIN_BLOCKLIST_DEFAULT)

    def test_corrupted_falls_back_to_default(self, tmp_path, monkeypatch):
        _write_blocklist(tmp_path, monkeypatch, "{invalid json")
        assert load_domain_sem_blocklist() == frozenset(frozenset(p) for p in DOMAIN_BLOCKLIST_DEFAULT)

        _write_blocklist(tmp_path, monkeypatch, '{"pairs": "not-list"}')
        assert load_domain_sem_blocklist() == frozenset(frozenset(p) for p in DOMAIN_BLOCKLIST_DEFAULT)

    def test_empty_falls_back_to_default(self, tmp_path, monkeypatch):
        _write_blocklist(tmp_path, monkeypatch, '{"pairs": []}')
        assert load_domain_sem_blocklist() == frozenset(frozenset(p) for p in DOMAIN_BLOCKLIST_DEFAULT)

    def test_valid_pairs_loads_and_lowercases(self, tmp_path, monkeypatch):
        _write_blocklist(
            tmp_path, monkeypatch,
            '{"pairs": [["制造业", "电商"], ["金融", "IT"]]}',
        )
        assert load_domain_sem_blocklist() == frozenset({
            frozenset({"制造业", "电商"}),
            frozenset({"金融", "it"}),
        })

    def test_dynamic_reload(self, tmp_path, monkeypatch):
        """修改配置文件后再次加载生效（每次调用重新读文件，不缓存）。"""
        _write_blocklist(tmp_path, monkeypatch, '{"pairs": [["制造业", "电商"]]}')
        assert load_domain_sem_blocklist() == frozenset({frozenset({"制造业", "电商"})})
        _write_blocklist(tmp_path, monkeypatch, '{"pairs": [["金融", "IT"]]}')
        assert load_domain_sem_blocklist() == frozenset({frozenset({"金融", "it"})})

    def test_invalid_pair_skipped_others_kept(self, tmp_path, monkeypatch):
        _write_blocklist(
            tmp_path, monkeypatch,
            '{"pairs": [["制造业", "电商"], ["只有一项"], 123, ["a", "b", "c"]]}',
        )
        assert load_domain_sem_blocklist() == frozenset({frozenset({"制造业", "电商"})})


class TestDomainBlocklistPair:
    def test_hit_both_orders(self, tmp_path, monkeypatch):
        """黑名单对无序等价命中（P1 演示：量子计算×占星术 双向命中）。"""
        _write_blocklist(tmp_path, monkeypatch, '{"pairs": [["量子计算", "占星术"]]}')
        assert domain_blocklist_pair("量子计算", "占星术")
        assert domain_blocklist_pair("占星术", "量子计算")

    def test_case_insensitive(self, tmp_path, monkeypatch):
        _write_blocklist(tmp_path, monkeypatch, '{"pairs": [["量子计算", "占星术"]]}')
        assert domain_blocklist_pair("量子计算", "占星术")

    def test_no_hit_outside_pair(self, tmp_path, monkeypatch):
        _write_blocklist(tmp_path, monkeypatch, '{"pairs": [["量子计算", "占星术"]]}')
        assert not domain_blocklist_pair("量子计算", "机器学习")

    def test_empty_never_hits(self, tmp_path, monkeypatch):
        _write_blocklist(tmp_path, monkeypatch, '{"pairs": [["量子计算", "占星术"]]}')
        assert not domain_blocklist_pair("", "占星术")
        assert not domain_blocklist_pair("量子计算", "")

    def test_missing_config_uses_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "app.services.matching.weights._BLOCKLIST_PATH",
            tmp_path / "not_exist.json",
        )
        assert domain_blocklist_pair("制造业", "电商")
        assert not domain_blocklist_pair("量子计算", "占星术")
