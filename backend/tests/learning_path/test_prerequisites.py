"""先修链展开单元测试（AL-M4-03，设计文档 §9.5）。"""

import pytest

from app.services.learning_path import prerequisites as mod


def _config(skills: dict, default_hours: float = 30.0) -> dict:
    return {"default_hours_per_skill": default_hours, "skills": skills}


@pytest.fixture(autouse=True)
def _clear_cache():
    mod.load_prerequisite_config.cache_clear()
    yield
    mod.load_prerequisite_config.cache_clear()


class TestPrerequisiteChain:
    def test_topo_order_prereqs_first(self, monkeypatch):
        """先修链按拓扑序展开：先修在前，目标技能本身不包含在链中。"""
        monkeypatch.setattr(
            mod,
            "load_prerequisite_config",
            lambda: _config(
                {
                    "深度学习": {"prerequisites": ["机器学习", "Python"]},
                    "机器学习": {"prerequisites": ["Python", "线性代数"]},
                }
            ),
        )
        chain = mod.prerequisite_chain("深度学习")
        assert "深度学习" not in chain
        assert set(chain) == {"机器学习", "Python", "线性代数"}
        assert chain.index("机器学习") > chain.index("线性代数")
        assert chain.index("机器学习") > chain.index("Python")

    def test_unknown_skill_returns_empty(self, monkeypatch):
        monkeypatch.setattr(mod, "load_prerequisite_config", lambda: _config({}))
        assert mod.prerequisite_chain("不存在的技能") == []

    def test_leaf_skill_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            mod,
            "load_prerequisite_config",
            lambda: _config({"Python": {"prerequisites": ["计算机基础"]}}),
        )
        assert mod.prerequisite_chain("计算机基础") == []

    def test_cycle_does_not_hang(self, monkeypatch):
        """环引用（A→B→A）防护：visited 集合保证不无限递归。"""
        monkeypatch.setattr(
            mod,
            "load_prerequisite_config",
            lambda: _config(
                {
                    "A": {"prerequisites": ["B"]},
                    "B": {"prerequisites": ["A"]},
                }
            ),
        )
        chain = mod.prerequisite_chain("A")
        assert set(chain) == {"B"}
        assert len(chain) == 1

    def test_multi_level_dedup(self, monkeypatch):
        """同一先修被多条路径引用时只出现一次。"""
        monkeypatch.setattr(
            mod,
            "load_prerequisite_config",
            lambda: _config(
                {
                    "目标": {"prerequisites": ["基础A", "基础B"]},
                    "基础A": {"prerequisites": ["根"]},
                    "基础B": {"prerequisites": ["根"]},
                }
            ),
        )
        chain = mod.prerequisite_chain("目标")
        assert chain.count("根") == 1


class TestBaseHours:
    def test_real_config_loads(self):
        """仓库内真实先修字典可加载（防配置路径漂移导致 FileNotFoundError）。"""
        cfg = mod.load_prerequisite_config()
        skills = cfg.get("skills") or {}
        assert skills
        assert "深度学习" in skills
        assert set(skills["深度学习"]["prerequisites"]) >= {"机器学习", "Python"}

    def test_default_hours(self, monkeypatch):
        monkeypatch.setattr(mod, "load_prerequisite_config", lambda: _config({}, default_hours=40.0))
        assert mod.base_hours("任意") == 40.0

    def test_per_skill_override(self, monkeypatch):
        monkeypatch.setattr(
            mod,
            "load_prerequisite_config",
            lambda: _config({"深度学习": {"hours": 80.0}}),
        )
        assert mod.base_hours("深度学习") == 80.0
        # 未收录字典的技能按白名单类别分层（机器学习 → AI/机器学习 70h）
        assert mod.base_hours("机器学习") == 70.0
        # 白名单外技能回落配置默认值
        assert mod.base_hours("任意不存在技能XYZ") == 30.0


class TestKeyNameConsistency:
    """AL-M5-06 先修字典键名 vs 图谱规范名一致性校验。

    图谱岗位技能名可能与字典键存在大小写/别名/后缀差异（如 "Golang" vs
    "Go"、"NSGs" vs "网络安全"），查找前须归一化，否则先修链落空。
    """

    def test_lowercase_key_lookup(self, monkeypatch):
        """大小写差异可解析到字典键（图谱名 "golang" → 键 "Go"）。"""
        monkeypatch.setattr(mod, "_prereq_index_cache", None)
        monkeypatch.setattr(
            mod,
            "load_prerequisite_config",
            lambda: _config({"Go": {"prerequisites": ["计算机基础"]}}),
        )
        assert mod.prerequisite_chain("golang") == ["计算机基础"]

    def test_canonical_alias_lookup(self, monkeypatch):
        """规范名别名可解析（图谱名 "网络安全" → 字典键 "NSGs"）。"""
        monkeypatch.setattr(mod, "_prereq_index_cache", None)
        assert mod.prerequisite_chain("网络安全")  # 图谱中文名 → NSGs 规范名命中先修链
        assert mod.prerequisite_chain("Nsgs")  # 大小写/别名变体同样命中

    def test_unknown_skill_empty_chain(self):
        """字典与白名单都无的技能 → 空链 + 默认学时（不崩）。"""
        assert mod.prerequisite_chain("完全不存在的玄幻技能XYZ") == []
        assert mod.base_hours("完全不存在的玄幻技能XYZ") == 30.0

    def test_resolve_skill_key_exact_first(self, monkeypatch):
        monkeypatch.setattr(mod, "_prereq_index_cache", None)
        monkeypatch.setattr(
            mod,
            "load_prerequisite_config",
            lambda: _config({"Go": {"hours": 40.0}}),
        )
        assert mod._resolve_skill_key("Go") == "Go"
        assert mod._resolve_skill_key("GO") == "Go"
        assert mod._resolve_skill_key("golang") == "Go"
        assert mod._resolve_skill_key("不存在的技能") is None
