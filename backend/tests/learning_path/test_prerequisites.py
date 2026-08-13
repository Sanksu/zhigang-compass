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
        monkeypatch.setattr(mod, "load_prerequisite_config", lambda: _config({}))
        assert mod.base_hours("未知技能") == 30.0

    def test_custom_default(self, monkeypatch):
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
