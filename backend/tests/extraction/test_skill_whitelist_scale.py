"""技能白名单规模门禁测试（设计文档 §6.3：第三道防线 500+ 标准技能）。

覆盖：
- 白名单规模 ≥ 500（单一事实源 configs/skill_whitelist.yaml）
- 软技能子集约束（SOFT_SKILL_WHITELIST ⊆ SKILL_WHITELIST，岗位本体维护 20 项）
- yaml 缺失时回退内置集，启动不失败
- 白名单条目格式（含 category 字段）与去重
"""

import sys
from pathlib import Path


# 回退白名单路径（与 dictionary.py 的 parents[3] 口径一致）
_BACKEND_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_BACKEND_DIR))

from app.services.extraction.dictionary import (
    SKILL_CATEGORY,
    SKILL_WHITELIST,
    SOFT_SKILL_WHITELIST,
    _SKILL_WHITELIST_PATH,
    _load_skill_whitelist,
    skill_category,
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

    def test_p2a_high_freq_unclassified_covered(self):
        # P2-A 高频未分类技能（评估报告 4.1：AI 编码工具 + Agent 生态）
        assert {"AI辅助编程", "GitHub Copilot", "Cursor", "Claude Code",
                "Codex", "ChatGPT", "GenAI", "Milvus", "dbt",
                "Databricks"}.issubset(SKILL_WHITELIST)

    def test_no_duplicates(self):
        assert len(SKILL_WHITELIST) == len({w.lower() for w in SKILL_WHITELIST}), \
            "白名单存在大小写重复项（normalize_skill 依赖小写映射唯一）"


class TestSkillCategory:
    """P0-1/P0-2/P0-3：技能分类映射（Skill.category 入图的单一事实源）。"""

    def test_all_entries_have_category(self):
        # 正常加载路径下每项都有非空分类（回退路径为空串是降级预期）
        assert all(SKILL_CATEGORY.values()), "存在无分类的白名单条目"

    def test_known_categories(self):
        assert skill_category("Python") == "编程语言"
        assert skill_category("消息队列") == "消息/中间件"
        assert skill_category("数据仓库") == "大数据"
        assert skill_category("操作系统") == "计算机基础"

    def test_reclassified_skills(self):
        # P0-2 重分类：归类错误修正
        assert skill_category("Verilog") == "硬件/芯片"
        assert skill_category("嵌入式开发") == "硬件/芯片"
        assert skill_category("自动化测试") == "测试"
        assert skill_category("微服务") == "计算机基础"

    def test_soft_skill_reclassified(self):
        # P0-3：数据分析思维从"数据分析/商业"移入软技能
        assert skill_category("数据分析思维") == "软技能"

    def test_unknown_returns_uncategorized(self):
        assert skill_category("不存在的技能XYZ") == "未分类"


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
        # 回退集为内置 169 项（降级路径规模不达 500 是预期，500+ 依赖 yaml）
        from app.services.extraction.dictionary import _FALLBACK_SKILL_WHITELIST

        monkeypatch.setattr(_SKILL_WHITELIST_PATH.__class__, "read_text",
                            lambda self, *a, **k: (_ for _ in ()).throw(OSError("缺失")))
        loaded = _load_skill_whitelist()
        assert set(loaded) == _FALLBACK_SKILL_WHITELIST
        assert all(v == "" for v in loaded.values()), "回退集无分类，category 应为空串"

    def test_fallback_when_yaml_empty(self, monkeypatch):
        # yaml 内容为空时回退内置集

        from app.services.extraction.dictionary import _FALLBACK_SKILL_WHITELIST

        monkeypatch.setattr(
            "app.services.extraction.dictionary.yaml.safe_load",
            lambda *a, **k: None,
        )
        loaded = _load_skill_whitelist()
        assert set(loaded) == _FALLBACK_SKILL_WHITELIST


class TestFallbackWhitelist:
    """内置回退集自身满足规模与软技能约束（yaml 缺失时的最后防线）。"""

    def test_fallback_scale(self):
        from app.services.extraction.dictionary import _FALLBACK_SKILL_WHITELIST

        # 169 = 170（原）− R（JD 基线决策支持 ②：单字符子串误报，2026-08-12）
        assert len(_FALLBACK_SKILL_WHITELIST) == 169

    def test_fallback_soft_subset(self):
        from app.services.extraction.dictionary import _FALLBACK_SKILL_WHITELIST

        assert SOFT_SKILL_WHITELIST.issubset(_FALLBACK_SKILL_WHITELIST)
