"""匹配权重加载回退测试。"""

from app.services.matching import weights
from app.services.matching.weights import DEFAULT_WEIGHTS, load_weights, load_sim_threshold


def _write_config(tmp_path, monkeypatch, content: str) -> None:
    cfg = tmp_path / "match_weights.json"
    cfg.write_text(content, encoding="utf-8")
    monkeypatch.setattr("app.services.matching.weights._CONFIG_PATH", cfg)


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
        """配置缺键时按默认值补齐。"""
        _write_config(tmp_path, monkeypatch, '{"w_must": 0.5}')
        assert load_weights() == (0.5, DEFAULT_WEIGHTS[1], DEFAULT_WEIGHTS[2])


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
