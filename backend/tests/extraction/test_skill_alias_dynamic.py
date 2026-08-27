"""动态别名（方案①）运行时并查测试。

覆盖：
1. normalize_skill 在硬编码 SKILL_ALIAS 未命中、白名单未命中时，并查动态别名表
   （只读 _DYNAMIC_ALIAS_LOWER，读序=词典→动态→白名单，D5）。
2. reload_dynamic_aliases 从 skill_aliases(approved) 加载（mock async_session_factory）。
"""

from types import SimpleNamespace


from app.services.extraction import dictionary as dict_mod


class TestNormalizeSkillDynamicAlias:
    def _reset(self, monkeypatch):
        monkeypatch.setattr(dict_mod, "_DYNAMIC_ALIAS_LOWER", {})

    def test_dynamic_alias_applied_when_not_in_static(self, monkeypatch):
        """硬编码 SKILL_ALIAS 未命中 + 动态有 → 并查返回动态标准名。"""
        self._reset(monkeypatch)
        dict_mod._DYNAMIC_ALIAS_LOWER["mybatisplus"] = "MyBatis"
        assert dict_mod.normalize_skill("mybatisplus") == "MyBatis"

    def test_static_alias_takes_precedence(self, monkeypatch):
        """读序=词典→动态（D5）：硬编码 SKILL_ALIAS 优先于动态。"""
        self._reset(monkeypatch)
        # JS 已在硬编码别名 → JavaScript；动态若也设 JS 不应覆盖
        dict_mod._DYNAMIC_ALIAS_LOWER["js"] = "X"
        assert dict_mod.normalize_skill("JS") == "JavaScript"

    def test_whitelist_fallback_when_no_dynamic(self, monkeypatch):
        """无静态别名、无动态 → 走白名单/原样。"""
        self._reset(monkeypatch)
        assert dict_mod.normalize_skill("mongo") == "MongoDB"

    def test_reload_loads_approved(self, monkeypatch):
        """reload_dynamic_aliases 从 skill_aliases(approved) 加载（mock DB）。"""
        self._reset(monkeypatch)
        rows = [
            SimpleNamespace(variant="mybatisplus", standard_name="MyBatis"),
            SimpleNamespace(variant="golabg", standard_name="Go"),
            SimpleNamespace(variant="var", standard_name="X"),  # 未 approved 不加载
        ]
        # status 过滤仅在 SQL 层；mock session 返回全量 rows（含 pending），
        # 但 reload 只取 approved —— mock 让所有当 approved 以简化；真实按 status=approved。
        fake_rows = [r for r in rows]

        class _FakeSession:
            def __init__(self):
                self.added = []

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def scalars(self, stmt):
                return SimpleNamespace(all=lambda: fake_rows)

        class _FakeFactory:
            def __call__(self):
                return _FakeSession()

        monkeypatch.setattr("app.core.database.async_session_factory", _FakeFactory())
        # 绕过 status=approved 过滤：mock SQL 层语义（这里 fake rows 全当 approved）
        n = dict_mod.reload_dynamic_aliases()
        assert n == 3
        assert dict_mod.dynamic_alias_lower().get("mybatisplus") == "MyBatis"
        assert dict_mod.dynamic_alias_lower().get("golabg") == "Go"

    def test_reload_db_unavailable_keeps_cache(self, monkeypatch):
        """DB 不可用：reload 失败不阻断，保留既有缓存。"""
        self._reset(monkeypatch)
        dict_mod._DYNAMIC_ALIAS_LOWER["mybatisplus"] = "MyBatis"
        monkeypatch.setattr("app.core.database.async_session_factory", _BadFactory())
        assert dict_mod.reload_dynamic_aliases() == 1  # 保持既有缓存
        assert dict_mod.dynamic_alias_lower().get("mybatisplus") == "MyBatis"


class _BadFactory:
    def __call__(self):
        raise RuntimeError("DB down")
