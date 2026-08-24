"""LLM 驱动黄金集派生脚本测试（PR9a1：确定性 gold 派生）。"""

import sys

sys.path.insert(0, "backend")

from scripts import freeze_llm_golden as fr


class TestFreezeDerivation:
    def test_classification_requires_no_label_intervention(self):
        """派生源全部为仓库内事实：白名单（category）+ 别名 + 先修/关系 YAML。"""
        from app.services.extraction.dictionary import SKILL_CATEGORY, SKILL_WHITELIST

        assert len(SKILL_WHITELIST) > 500
        assert all(n in SKILL_CATEGORY for n in SKILL_WHITELIST)
        samples = fr.classification_samples(
            {n: SKILL_CATEGORY[n] for n in SKILL_WHITELIST}, seed=7,
        )
        assert samples
        assert all(s["gold_category"] for s in samples)
        # 有短词切片
        assert any(s["slice"] == "short_ascii" for s in samples)

    def test_alias_pairs_merge_gold(self):
        from app.services.extraction.dictionary_data import SKILL_ALIAS

        pairs = fr.normalization_pairs(SKILL_ALIAS, seed=7)
        merge = [p for p in pairs if p["gold_action"] == "merge"]
        keep = [p for p in pairs if p["gold_action"] == "keep"]
        assert merge and keep
        assert all(p["gold_standard"] for p in merge)
        assert all(len(p["variant"]) <= 6 for p in keep)

    def test_relation_gold_from_yml(self, tmp_path):
        pairs = fr.relation_pairs(seed=7)
        relations = {p["gold_relation"] for p in pairs}
        assert {"PREREQUISITE_OF", "BELONGS_TO", "ALTERNATIVE_OF", "NONE"} <= relations
        none_rows = [p for p in pairs if p["gold_relation"] == "NONE"]
        assert none_rows and "跨类规则推断" in none_rows[0]["source_note"]

    def test_freeze_roundtrip_deterministic(self):
        from app.services.extraction.dictionary import SKILL_CATEGORY, SKILL_WHITELIST
        from app.services.extraction.dictionary_data import SKILL_ALIAS

        a = fr.classification_samples(
            {n: SKILL_CATEGORY[n] for n in SKILL_WHITELIST}, seed=42,
        )
        b = fr.classification_samples(
            {n: SKILL_CATEGORY[n] for n in SKILL_WHITELIST}, seed=42,
        )
        assert a == b
        assert fr.normalization_pairs(SKILL_ALIAS, seed=1) == fr.normalization_pairs(SKILL_ALIAS, seed=1)
