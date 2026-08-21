"""数据质量阈值参数化测试。

覆盖 configs/data_quality_thresholds.json 的加载、运行时覆盖与缺失回退：
- 默认值（设计文档 §4.2/§4.7）与配置一致；
- simhash / temporal_detector 未显式传阈值时取运行时配置；
- 配置缺失/损坏时回退模块默认值（不抛异常，不阻断检测）。
"""

import json

import pytest

from app.services.data_quality.simhash import SimHashIndex, is_duplicate, simhash64
from app.services.data_quality.thresholds import (
    _CONFIG_PATH,
    _load_config,
    load_embed_dedup_threshold,
    load_hamming_threshold,
    load_sai_stale_threshold,
    load_zombie_consecutive_periods,
)
from app.services.data_quality.temporal_detector import (
    classify_sai,
    detect_plagiarism,
    detect_zombie_jd,
)
from app.services.data_quality.schemas import JDSkillSet


class TestConfigFile:
    def test_config_exists_and_valid(self):
        assert _CONFIG_PATH.exists(), "configs/data_quality_thresholds.json 缺失"
        data = _load_config()
        assert data
        # 关键阈值键必须存在且数值合法
        for key in (
            "hamming_threshold",
            "embed_dedup_threshold",
            "sai_stale_threshold",
            "sai_obsolete_threshold",
            "recent_window_days",
            "zombie_jaccard_threshold",
            "zombie_sai_threshold",
            "zombie_consecutive_periods",
            "plagiarism_days",
        ):
            assert key in data, f"缺少阈值键 {key}"

    def test_config_matches_design_defaults(self):
        assert load_hamming_threshold() == 3
        assert load_embed_dedup_threshold() == 0.9
        assert load_sai_stale_threshold() == 1.5
        assert load_zombie_consecutive_periods() == 4


class TestConfigDrivenThreshold:
    def test_is_duplicate_uses_runtime_config(self, monkeypatch, tmp_path):
        """未显式传阈值时，判定取运行时配置值。"""
        cfg = tmp_path / "thresholds.json"
        cfg.write_text(json.dumps({"hamming_threshold": 2}), encoding="utf-8")
        monkeypatch.setattr("app.services.data_quality.thresholds._CONFIG_PATH", cfg)

        desc = "负责核心业务系统后端开发 使用 Python 技术栈 高并发分布式系统"
        a = simhash64("Python 后端开发工程师 " + desc)
        b = simhash64("Python后端开发工程师 " + desc)  # 仅空格差异 → 近似重复
        # 阈值 2 下仅空格差异的文本应判重复（运行时配置已生效）
        assert is_duplicate(a, b)

    def test_simhash_index_resolves_config_threshold(self, monkeypatch, tmp_path):
        """SimHashIndex 未传阈值时按运行时配置构造（抽屉原理不变量保持）。"""
        cfg = tmp_path / "thresholds.json"
        cfg.write_text(json.dumps({"hamming_threshold": 2}), encoding="utf-8")
        monkeypatch.setattr("app.services.data_quality.thresholds._CONFIG_PATH", cfg)

        index = SimHashIndex()
        assert index.threshold == 2
        # 显式传参仍可覆盖运行时配置
        assert SimHashIndex(threshold=3).threshold == 3

    def test_classify_sai_uses_runtime_config(self, monkeypatch, tmp_path):
        """SAI 阈值缺省取运行时配置（stale 1.3 → 1.4 判 stale）。"""
        cfg = tmp_path / "thresholds.json"
        cfg.write_text(
            json.dumps({"sai_stale_threshold": 1.3, "sai_obsolete_threshold": 2.0}),
            encoding="utf-8",
        )
        monkeypatch.setattr("app.services.data_quality.thresholds._CONFIG_PATH", cfg)

        assert classify_sai(1.4).label == "content_stale"
        # 显式覆盖仍生效
        assert classify_sai(1.4, stale_threshold=1.5).label == "fresh"

    def test_detect_zombie_uses_runtime_config(self, monkeypatch, tmp_path):
        """僵尸 JD 周期数/阈值取运行时配置（3 期即判）。"""
        cfg = tmp_path / "thresholds.json"
        cfg.write_text(
            json.dumps(
                {
                    "zombie_consecutive_periods": 3,
                    "zombie_jaccard_threshold": 0.9,
                    "zombie_sai_threshold": 1.5,
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr("app.services.data_quality.thresholds._CONFIG_PATH", cfg)

        history = [{"s1", "s2", "s3"} for _ in range(2)]  # 2 历史 + 1 当前 = 3 期
        result = detect_zombie_jd(history, {"s1", "s2", "s3"}, sai=1.6)
        assert result.is_zombie is True
        assert result.consecutive_periods == 3

    def test_detect_plagiarism_uses_runtime_config(self, monkeypatch, tmp_path):
        """抄袭天数窗口取运行时配置（60 天即判）。"""
        from datetime import date, timedelta

        cfg = tmp_path / "thresholds.json"
        cfg.write_text(json.dumps({"plagiarism_days": 60}), encoding="utf-8")
        monkeypatch.setattr("app.services.data_quality.thresholds._CONFIG_PATH", cfg)

        new = JDSkillSet(
            jd_id="n",
            position_name="后端",
            publish_date=date.today() - timedelta(days=70),
            skills=["Python"],
        )
        old = JDSkillSet(
            jd_id="o",
            position_name="后端",
            publish_date=date.today() - timedelta(days=200),
            skills=["Python", "Java"],
        )
        assert detect_plagiarism(new, old).is_plagiarism is True


class TestConfigFallback:
    def test_missing_config_falls_back_to_defaults(self, monkeypatch, tmp_path):
        """配置缺失时回退设计文档默认值，不抛异常。"""
        missing = tmp_path / "not_exists.json"
        monkeypatch.setattr("app.services.data_quality.thresholds._CONFIG_PATH", missing)

        assert load_hamming_threshold() == 3
        assert load_embed_dedup_threshold() == 0.9
        assert load_sai_stale_threshold() == 1.5
        assert load_zombie_consecutive_periods() == 4

    def test_broken_config_falls_back_to_defaults(self, monkeypatch, tmp_path):
        """配置损坏（非法 JSON）时回退默认值，不抛异常。"""
        broken = tmp_path / "broken.json"
        broken.write_text("{not valid json", encoding="utf-8")
        monkeypatch.setattr("app.services.data_quality.thresholds._CONFIG_PATH", broken)

        assert load_hamming_threshold() == 3
        assert load_embed_dedup_threshold() == 0.9
