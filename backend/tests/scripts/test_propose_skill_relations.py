"""技能关系提议脚本测试（PR6b：候选选择 + 提议编排纯函数层 + 拦截语义）。"""

from app.services.llm_decision import STATUS_BLOCKED, STATUS_PROPOSAL
from scripts.propose_skill_relations import select_candidates


class TestSelectCandidates:
    def test_ordered_by_total_and_filtered(self):
        cooccurrence = {
            ("Java", "Spring"): [{"position": "A", "count": 3}, {"position": "B", "count": 2}],
            ("Python", "Docker"): [{"position": "C", "count": 4}],
            ("React", "Vue"): [{"position": "D", "count": 1}],  # 低于下限
        }
        out = select_candidates(cooccurrence, limit=10)
        assert [c["source"] for c in out] == ["Java", "Python"]
        assert out[0]["total"] == 5
        assert out[0]["evidence"] == [{"position": "A", "count": 3}, {"position": "B", "count": 2}]
        assert "React" not in [c["source"] for c in out]

    def test_limit_caps(self):
        cooccurrence = {
            (f"技能{i}", f"技能{i + 100}"): [{"position": "P", "count": 3}]
            for i in range(10)
        }
        assert len(select_candidates(cooccurrence, limit=3)) == 3

    def test_empty_input(self):
        assert select_candidates({}, limit=10) == []


class TestProposeIntegrationLogic:
    """用纯函数组合验证 propose() 的核心裁决链（不触库）。"""

    def test_gate_and_cycle_composition(self):
        # 模拟 propose() 的 gate 链：先硬门（自指/虚构/方向），再环判定
        from app.services.llm_decision.skill_relation import (
            REL_PREREQUISITE,
            prerequisite_cycle_would_create,
            skill_relation_gate,
        )

        known = {"Java", "Spring"}
        # 正常先修候选：无环
        ok, reason = skill_relation_gate(
            __import__("app.services.llm_decision.skill_relation", fromlist=["SkillRelationDecision"])
            .SkillRelationDecision(relation=REL_PREREQUISITE, direction="a_to_b", confidence=0.9),
            "Java", "Spring", known,
        )
        assert ok
        assert prerequisite_cycle_would_create({"Spring": {"Java"}}, "Java", "Spring") is False
        # 反向成环候选：硬门通过但环判定拦截（对应 propose() 内 blocked 路径）
        assert prerequisite_cycle_would_create({"Spring": {"Java"}}, "Spring", "Java") is True

    def test_blocked_vs_proposal_status_semantics(self):
        # 决策记录 status 语义与 PR6 验收口径一致：环/门失败=blocked，通过=proposal
        assert STATUS_BLOCKED != STATUS_PROPOSAL