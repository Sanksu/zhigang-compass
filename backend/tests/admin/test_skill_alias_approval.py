"""技能别名回写审批测试（方案①：skill_normalize + kind=alias → approved 落 skill_aliases）。

asyncio.run 直调 mod._approve_skill_alias（注入 fake async session），对齐
test_skill_classify_approval.py 的写法。解耦 DB/LLM：monkeypatch 掉
known_standard_names 与 reload_dynamic_aliases。

成功返回 APIResponse（.data）；失败返回 JSONResponse（.status_code / .body）。
"""

import asyncio
from types import SimpleNamespace


from app.api.v1.admin_routes import llm_decisions as mod

_OPERATOR = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _alias_record(status="proposal", variant="JS", standard="JavaScript"):
    return SimpleNamespace(
        id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        domain="skill_normalize",
        status=status,
        entity_id=variant,
        entity_type="skill",
        structured_output={"action": "merge", "target_standard": standard,
                           "kind": "alias", "confidence": 0.9},
        reviewer="", review_reason="", effects_applied=False,
    )


class _FakeSession:
    def __init__(self, record, existing_aliases=()):
        self._record = record
        self._existing_aliases = list(existing_aliases)
        self.added: list = []
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, model, decision_id):
        return self._record

    async def scalars(self, stmt):
        rows = self._existing_aliases
        return SimpleNamespace(
            all=lambda: rows, first=lambda: rows[0] if rows else None,
        )

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


class TestApproveSkillAlias:
    def test_approve_writes_skill_alias(self, monkeypatch):
        """正常批准：落 skill_aliases(approved) + 决策 approved。"""
        from app.services.llm_decision import skill_normalize as sn
        from app.services.extraction import dictionary as dict_mod

        monkeypatch.setattr(sn, "known_standard_names", lambda: {"JavaScript"})
        monkeypatch.setattr(dict_mod, "reload_dynamic_aliases", lambda: 1)

        rec = _alias_record()
        db = _FakeSession(rec)
        result = asyncio.run(mod._approve_skill_alias(db, rec, "同意", _OPERATOR))
        assert result.data["variant"] == "JS"
        assert result.data["standard"] == "JavaScript"
        assert db.committed is True
        alias_rows = [a for a in db.added if a.__class__.__name__ == "SkillAlias"]
        assert alias_rows and alias_rows[0].status == "approved"
        assert rec.status == "approved"
        assert rec.reviewer == _OPERATOR

    def test_approve_variant_missing(self, monkeypatch):
        """variant/target 缺失 → 校验错误（JSONResponse）。"""
        rec = _alias_record(variant="", standard="JavaScript")
        db = _FakeSession(rec)
        result = asyncio.run(mod._approve_skill_alias(db, rec, "同意", _OPERATOR))
        assert result.status_code == 422
        assert db.committed is False

    def test_approve_standard_not_in_known(self, monkeypatch):
        """standard 不在权威标准名集合 → 校验错误（防虚构）。"""
        from app.services.llm_decision import skill_normalize as sn

        monkeypatch.setattr(sn, "known_standard_names", lambda: {"JavaScript"})
        rec = _alias_record(standard="JavascriptX")
        db = _FakeSession(rec)
        result = asyncio.run(mod._approve_skill_alias(db, rec, "同意", _OPERATOR))
        assert result.status_code == 422
        assert db.committed is False

    def test_approve_duplicate_variant(self, monkeypatch):
        """unique(variant)：已存在该 variant → 冲突。"""
        from app.services.llm_decision import skill_normalize as sn
        from app.services.extraction import dictionary as dict_mod

        monkeypatch.setattr(sn, "known_standard_names", lambda: {"JavaScript"})
        monkeypatch.setattr(dict_mod, "reload_dynamic_aliases", lambda: 1)
        existing = SimpleNamespace(variant="JS", proposal_id="old", standard_name="JavaScript")
        rec = _alias_record()
        db = _FakeSession(rec, existing_aliases=[existing])
        result = asyncio.run(mod._approve_skill_alias(db, rec, "同意", _OPERATOR))
        assert result.status_code == 409
        assert db.committed is False
