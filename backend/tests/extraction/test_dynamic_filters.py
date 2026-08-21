"""动态过滤层（dict-guard 机制层）单测。

覆盖：空层/损坏容错、is_noise_skill 叠加语义（动态保护 > 静态停用词；
白名单 > 动态停用词）、TTL 缓存与本进程写入即时生效、add/remove 条目管理。
"""

import json

import pytest
from app.services.extraction import dynamic_filters as df
from app.services.extraction.dictionary import SKILL_STOPWORDS, is_noise_skill


@pytest.fixture()
def filters_path(tmp_path, monkeypatch):
    """把动态层文件指到临时路径并清空缓存，测试互不串扰。"""
    path = tmp_path / "skill_filters_dynamic.json"
    monkeypatch.setattr(df, "_FILTERS_PATH", path)
    df.invalidate_cache()
    return path


def _write_file(path, payload: dict) -> None:
    """模拟其他进程直接写盘（绕过本模块 API，验证 TTL 感知）。"""
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class TestEmptyLayer:
    def test_missing_file_is_empty_layer(self, filters_path):
        assert df.is_dynamically_blocked("某词") is False
        assert df.is_dynamically_protected("某词") is False
        assert df.get_dynamic_terms() == {"blocked": [], "protected": []}

    def test_corrupt_file_treated_as_empty(self, filters_path):
        filters_path.write_text("{not-json", encoding="utf-8")
        df.invalidate_cache()
        assert df.is_dynamically_blocked("某词") is False
        assert df.is_dynamically_protected("某词") is False


class TestMatching:
    def test_blocked_and_protected_hits(self, filters_path):
        _write_file(filters_path, {
            "blocked": [{"term": "数据中台"}],
            "protected": [{"term": "微信小程序"}],
        })
        df.invalidate_cache()
        assert df.is_dynamically_blocked("数据中台") is True
        assert df.is_dynamically_protected("微信小程序") is True

    def test_latin_case_insensitive(self, filters_path):
        _write_file(filters_path, {
            "blocked": [{"term": "LowCode"}],
            "protected": [],
        })
        df.invalidate_cache()
        assert df.is_dynamically_blocked("lowcode") is True
        assert df.is_dynamically_blocked("LOWCODE") is True

    def test_non_hit(self, filters_path):
        _write_file(filters_path, {
            "blocked": [{"term": "数据中台"}],
            "protected": [{"term": "微信小程序"}],
        })
        df.invalidate_cache()
        assert df.is_dynamically_blocked("微信小程序") is False
        assert df.is_dynamically_protected("数据中台") is False


class TestNoiseSkillWiring:
    """is_noise_skill 叠加语义（唯一接线点的行为契约）。"""

    def test_dynamic_block_flags_noise(self, filters_path):
        _write_file(filters_path, {"blocked": [{"term": "数字化转型的"}], "protected": []})
        df.invalidate_cache()
        assert is_noise_skill("数字化转型的") is True

    def test_whitelist_beats_dynamic_block(self, filters_path):
        # 纵深防御：即使动态停用词误含白名单词（写入侧硬门禁应拦），白名单仍胜出
        _write_file(filters_path, {"blocked": [{"term": "Python"}], "protected": []})
        df.invalidate_cache()
        assert is_noise_skill("Python") is False

    def test_dynamic_protection_overrides_static_stopword(self, filters_path):
        # 前提锚点：操作系统 属静态停用词（test_post_processor.TestStopwordInterception 同款）
        assert "操作系统" in SKILL_STOPWORDS
        assert is_noise_skill("操作系统") is True
        _write_file(filters_path, {"blocked": [], "protected": [{"term": "操作系统"}]})
        df.invalidate_cache()
        assert is_noise_skill("操作系统") is False

    def test_static_stopword_still_blocks_without_protection(self, filters_path):
        assert is_noise_skill("操作系统") is True


class TestEntryManagement:
    def test_add_and_remove_roundtrip(self, filters_path):
        df.add_entry("blocked", "低代码平台", reason="测试", source="manual")
        assert df.is_dynamically_blocked("低代码平台") is True
        assert df.remove_entry("blocked", "低代码平台") is True
        assert df.is_dynamically_blocked("低代码平台") is False

    def test_remove_missing_returns_false(self, filters_path):
        assert df.remove_entry("blocked", "不存在的词") is False

    def test_add_dedupes_case_insensitive(self, filters_path):
        df.add_entry("blocked", "FooBar", reason="第一次", source="manual")
        df.add_entry("blocked", "foobar", reason="第二次", source="manual")
        terms = df.get_dynamic_terms()
        assert len(terms["blocked"]) == 1
        assert terms["blocked"][0]["reason"] == "第二次"

    def test_invalid_kind_raises(self, filters_path):
        with pytest.raises(ValueError):
            df.add_entry("nope", "x", reason="r", source="manual")
        with pytest.raises(ValueError):
            df.remove_entry("nope", "x")

    def test_written_file_is_valid_json_with_version(self, filters_path):
        df.add_entry("protected", "某保护词", reason="r", source="dict_guard", operator="admin")
        data = json.loads(filters_path.read_text(encoding="utf-8"))
        assert data["version"] >= 1
        assert data["protected"][0]["term"] == "某保护词"
        assert data["protected"][0]["source"] == "dict_guard"


class TestTtlCache:
    def test_same_process_write_visible_immediately(self, filters_path):
        assert df.is_dynamically_blocked("新词") is False
        df.add_entry("blocked", "新词", reason="r", source="manual")
        # add_entry 内部 invalidate_cache，本进程无需等 TTL
        assert df.is_dynamically_blocked("新词") is True

    def test_external_write_stale_until_ttl_expires(self, filters_path, monkeypatch):
        _write_file(filters_path, {"blocked": [{"term": "外部词"}], "protected": []})
        df.invalidate_cache()
        assert df.is_dynamically_blocked("外部词") is True  # 路径变化已重载

        _write_file(filters_path, {"blocked": [], "protected": []})
        # 缓存新鲜期内读到旧值（模拟其他进程写盘后本进程 ≤30s 窗口）
        assert df.is_dynamically_blocked("外部词") is True
        # TTL 过期后重读
        monkeypatch.setattr(df, "_cache_at", 0.0)
        assert df.is_dynamically_blocked("外部词") is False
