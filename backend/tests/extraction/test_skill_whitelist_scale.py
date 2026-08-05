"""技能白名单规模门禁测试（设计文档 §6.3：第三道防线 500+ 标准技能）。

覆盖：
- 白名单规模 ≥ 500（单一事实源 configs/skill_whitelist.yaml）
- 软技能子集约束（SOFT_SKILL_WHITELIST ⊆ SKILL_WHITELIST，岗位本体维护 20 项）
- yaml 缺失时回退内置集，启动不失败
- 白名单条目格式（含 category 字段）与去重
"""

import sys
from pathlib import Path

import pytest

# 回退白名单路径（与 dictionary.py 的 parents[3] 口径一致）
_BACKEND_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_BACKEND_DIR))

from app.services.extraction.dictionary import (
    SKILL_WHITELIST,
    SOFT_SKILL_WHITELIST,
    _SKILL_WHITELIST_PATH,
    _load_skill_whitelist,
)


class TestSkillWhitelistScale:
    """设计 §6.3：规则白名单 500+ 标准技能（幻觉防控第三道防线）。"""

    def test_whitelist_scale(self):
        assert len(SKILL_WHITELIST) >= 500, "白名单规模不达标（设计 §6.3 要求 500+）"

    def test_soft_skills_subset(self):
        # 软技能是技能白名单的标记性子集，JD/简历抽取共用同一枚举域
        assert SOFT_SKILL_WHITELIST.issubset(SKILL_WHITELIST)

    def test_high_freq_real_skills_covered(self):
        # 历史图谱审计中白名单外的高频真实技能（回归防线，防外置后遗漏）
        assert {"SQL", "AWS", "Azure", "GCP", "Linux", "Tableau", "Agile",
                "Excel", "NoSQL", "SAS", "ETL", "Snowflake", "DevOps",
                "Power BI", "RESTful API", "JSON", "API", "JIRA",
                "Maven", "JUnit", "Hibernate", "Pandas", "Transformer",
                "AI", "C", "MATLAB"}.issubset(SKILL_WHITELIST)

    def test_no_duplicates(self):
        assert len(SKILL_WHITELIST) == len({w.lower() for w in SKILL_WHITELIST}), \
            "白名单存在大小写重复项（normalize_skill 依赖小写映射唯一）"


class TestSkillWhitelistYaml:
    """yaml 单一事实源加载与回退。"""

    def test_yaml_loads_with_category(self):
        # yaml 条目应带 category（schema.cypher skill.category 索引使用）
        import yaml

        data = yaml.safe_load(_SKILL_WHITELIST_PATH.read_text(encoding="utf-8")) or {}
        skills = data.get("skills") or []
        assert len(skills) == len(SKILL_WHITELIST), "yaml 与加载结果数量不一致"
        for s in skills:
            assert isinstance(s, dict) and s.get("name"), f"非法条目: {s}"
            assert s.get("category"), f"条目缺 category: {s['name']}"

    def test_fallback_when_yaml_missing(self, monkeypatch):
        # yaml 缺失时回退内置集，启动不失败（第三道防线降级不阻塞抽取）。
        # 回退集为原硬编码 170 项（降级路径规模不达 500 是预期，500+ 依赖 yaml）
        from app.services.extraction.dictionary import _FALLBACK_SKILL_WHITELIST

        monkeypatch.setattr(_SKILL_WHITELIST_PATH.__class__, "read_text",
                            lambda self, *a, **k: (_ for _ in ()).throw(OSError("缺失")))
        loaded = _load_skill_whitelist()
        assert loaded == _FALLBACK_SKILL_WHITELIST

    def test_fallback_when_yaml_empty(self, monkeypatch):
        # yaml 内容为空时回退内置集
        import yaml

        from app.services.extraction.dictionary import _FALLBACK_SKILL_WHITELIST

        monkeypatch.setattr(
            "app.services.extraction.dictionary.yaml.safe_load",
            lambda *a, **k: None,
        )
        loaded = _load_skill_whitelist()
        assert loaded == _FALLBACK_SKILL_WHITELIST


class TestFallbackWhitelist:
    """内置回退集自身满足规模与软技能约束（yaml 缺失时的最后防线）。"""

    def test_fallback_scale(self):
        from app.services.extraction.dictionary import _FALLBACK_SKILL_WHITELIST

        assert len(_FALLBACK_SKILL_WHITELIST) == 170

    def test_fallback_soft_subset(self):
        from app.services.extraction.dictionary import _FALLBACK_SKILL_WHITELIST

        assert SOFT_SKILL_WHITELIST.issubset(_FALLBACK_SKILL_WHITELIST)
