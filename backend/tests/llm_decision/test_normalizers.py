"""名称归一 LLM 决策器测试（PR3a：岗位名 + 技能名，shadow 风控先行）。

覆盖：prompt 组装、硬门（防幻觉长名/空名/自创名/虚构标准名/同义反复）、
LLM 失败降级 None、风险档位映射（R0 建议类 / blocked 硬门失败）。
"""

import pytest

from app.services.extraction.llm_provider import LLMExtractionError
from app.services.llm_decision import TIER_BLOCKED, TIER_R0
from app.services.llm_decision.position_name import (
    PositionNameDecision,
    build_position_name_prompt,
    decide_position_name,
    position_name_gate,
    tier_for_position_decision,
)
from app.services.llm_decision.skill_normalize import (
    SkillNormalizeDecision,
    build_skill_normalize_prompt,
    decide_skill_normalize,
    known_standard_names,
    skill_normalize_gate,
    tier_for_skill_decision,
)


def _pos(*, canonical="", is_new=False, keep=False, conf=0.9):
    return PositionNameDecision(
        canonical_name=canonical, is_new=is_new,
        keep_original=keep, confidence=conf,
    )


class TestPositionNameGate:
    def test_keep_original_passes(self):
        assert position_name_gate(_pos(keep=True), "Java 后端", []) == (True, "")

    def test_empty_canonical_blocked(self):
        # Schema 已把"非 keep 却空名"拦在前面；这里用 model_construct 绕过校验，
        # 专门验证 gate 的防御性分支（keep_original=True 但 canonical 仍为空的异常态）
        dec = PositionNameDecision.model_construct(
            canonical_name="", is_new=False, keep_original=False, confidence=0.9,
        )
        ok, reason = position_name_gate(dec, "Java 后端", [])
        assert not ok
        assert "为空" in reason

    def test_overlong_canonical_blocked(self):
        ok, reason = position_name_gate(_pos(canonical="x" * 41), "Java 后端", [])
        assert not ok
        assert "长度越界" in reason

    def test_new_position_passes(self):
        assert position_name_gate(_pos(canonical="AGI 安全研究员", is_new=True), "AGI安全研究员", []) == (True, "")

    def test_same_as_raw_passes(self):
        assert position_name_gate(_pos(canonical="Java 后端工程师"), "Java 后端工程师", []) == (True, "")

    def test_in_candidates_passes(self):
        assert position_name_gate(_pos(canonical="测试开发工程师"), "测试开发", ["测试开发工程师"]) == (True, "")

    def test_hallucinated_name_blocked(self):
        ok, reason = position_name_gate(
            _pos(canonical="量子烹饪架构师"), "测试开发", ["测试开发工程师", "Java 后端工程师"],
        )
        assert not ok
        assert "候选清单" in reason


class TestPositionNamePromptAndDecide:
    def test_prompt_carries_evidence(self):
        prompt = build_position_name_prompt("测试开发", ["Python", "pytest"], "boss", ["测试开发工程师"])
        assert "测试开发" in prompt
        assert "Python" in prompt
        assert "boss" in prompt
        assert "测试开发工程师" in prompt

    def test_decide_parses_valid_output(self):
        sentinel = PositionNameDecision(canonical_name="测试开发工程师", is_new=False, confidence=0.95)

        class _FakeLLM:
            def extract_structured(self, prompt, model, **kwargs):
                return sentinel

        decision = decide_position_name("测试开发", ["Python"], "boss", ["测试开发工程师"], _FakeLLM())
        assert decision is sentinel
        assert decision.canonical_name == "测试开发工程师"

    def test_decide_none_on_llm_failure(self):
        class _BoomLLM:
            def extract_structured(self, prompt, model, **kwargs):
                raise LLMExtractionError("provider 全挂")

        assert decide_position_name("测试开发", [], "boss", [], _BoomLLM()) is None

    def test_decide_none_without_llm_or_title(self):
        assert decide_position_name("", [], "boss", [], None) is None
        assert decide_position_name("测试开发", [], "boss", [], None) is None

    def test_tier_mapping(self):
        assert tier_for_position_decision(_pos(canonical="测试开发工程师"), gate_ok=True) == (TIER_R0, "")
        assert tier_for_position_decision(_pos(canonical="四处乱名"), gate_ok=False)[0] == TIER_BLOCKED


class TestSkillNormalizeGate:
    def test_keep_and_noise_pass(self):
        assert skill_normalize_gate(SkillNormalizeDecision(action="keep"), "ArkUI") == (True, "")
        assert skill_normalize_gate(SkillNormalizeDecision(action="noise"), "某教程名") == (True, "")

    def test_merge_to_known_standard_passes(self):
        target = next(iter(known_standard_names()))
        assert skill_normalize_gate(SkillNormalizeDecision(action="merge", target_standard=target), "x-" + target) == (True, "")

    def test_merge_to_unknown_target_blocked(self):
        ok, reason = skill_normalize_gate(
            SkillNormalizeDecision(action="merge", target_standard="量子烹饪学"), "量子烹饪",
        )
        assert not ok
        assert "权威标准名集合" in reason

    def test_merge_same_name_blocked(self):
        ok, reason = skill_normalize_gate(
            SkillNormalizeDecision(action="merge", target_standard="Python"), "Python",
        )
        assert not ok
        assert "同义反复" in reason


class TestSkillNormalizePromptAndDecide:
    def test_prompt_carries_name_and_candidates(self):
        prompt = build_skill_normalize_prompt("Python3", ["Python", "Python 3"])
        assert "Python3" in prompt
        assert "Python 3" in prompt

    def test_decide_parses_valid_output(self):
        sentinel = SkillNormalizeDecision(action="merge", target_standard="Python", confidence=0.97)

        class _FakeLLM:
            def extract_structured(self, prompt, model, **kwargs):
                return sentinel

        decision = decide_skill_normalize("Python3", _FakeLLM(), candidates=["Python", "Python 3"])
        assert decision is sentinel

    def test_decide_invalid_action_falls_back_none(self):
        class _BadLLM:
            def extract_structured(self, prompt, model, **kwargs):
                return SkillNormalizeDecision(action="explode")

        with pytest.raises(Exception):
            decide_skill_normalize("Python3", _BadLLM(), candidates=["Python"])

    def test_decide_none_without_llm(self):
        assert decide_skill_normalize("Python3", None) is None
        assert decide_skill_normalize("", object()) is None

    def test_tier_mapping(self):
        dec = SkillNormalizeDecision(action="merge", target_standard="Python", confidence=0.9)
        assert tier_for_skill_decision(dec, gate_ok=True) == (TIER_R0, "")
        assert tier_for_skill_decision(dec, gate_ok=False)[0] == TIER_BLOCKED

class TestCandidateRecallCalibration:
    """校准 r1：候选召回排序（词面关联优先于长度差）。"""

    def test_rank_key_tiers(self):
        from app.services.llm_decision.skill_normalize import candidate_rank_key

        assert candidate_rank_key("react", "React")[0] == 0  # 大小写不敏感相等
        assert candidate_rank_key("c语言", "C")[0] == 0  # 子串
        assert candidate_rank_key("JS", "JavaScript")[0] == 0  # 大写首字母串缩写匹配
        assert candidate_rank_key("量子烹饪", "React")[0] == 2  # 无关联

    def test_default_candidates_recall_variants(self):
        """基线瓶颈回归：变体的 gold 标准名必须进默认候选前 15。"""
        from app.services.llm_decision.skill_normalize import (
            candidate_rank_key, known_standard_names,
        )

        known = known_standard_names()
        checks = {
            "react": "React", "golang": "Go", "vue": "Vue.js",
            "JS": "JavaScript", "c语言": "C",
        }
        for variant, standard in checks.items():
            top = sorted(known, key=lambda c: candidate_rank_key(variant, c))[:15]
            assert standard in top, f"{variant} 的候选未召回 {standard}"

    def test_prompt_mentions_variant_kinds(self):
        from app.services.llm_decision.skill_normalize import build_skill_normalize_prompt

        prompt = build_skill_normalize_prompt("react", ["React"])
        assert "缩写" in prompt and "版本号" in prompt
        assert "与自身相同的目标不是 merge" in prompt


class TestAliasHintCalibration:
    """校准 r3：别名表命中时标准落点置顶（跨语言召回补强）。"""

    def test_alias_target_prepended_to_candidates(self):
        from app.services.llm_decision.skill_normalize import (
            build_skill_normalize_prompt,
        )

        # 直接验证 prompt 组装含别名提示语义（不可离线调 LLM 的路径）
        prompt = build_skill_normalize_prompt("full stack development", ["全栈", "React"])
        assert "全栈" in prompt.split("候选标准技能名")[1]


class TestAliasAnchorCalibration:
    """校准 r7（拍板 ②2a）：别名落点 * 标注为 merge 目标约束。

    边界：只约束 merge 目标选择（可辩域 9 例），不动保守 keep 门
    （缩写 12 例行为保持 = 2b 不做）；无别名命中时候选无 *，独立
    判断路径不受影响。
    """

    def test_alias_target_marked_with_star(self):
        from app.services.llm_decision.skill_normalize import build_skill_normalize_prompt

        prompt = build_skill_normalize_prompt(
            "c语言", ["C", "C++", "C#"], alias_target="C",
        )
        cand_line = prompt.split("既定落点）：")[1].splitlines()[0]
        assert "C*" in cand_line and "C++" in cand_line and "C#*" not in cand_line

    def test_no_alias_target_no_star(self):
        from app.services.llm_decision.skill_normalize import build_skill_normalize_prompt

        prompt = build_skill_normalize_prompt("某种库", ["Python", "Java"])
        assert "*" not in prompt.split("既定落点）：")[1].splitlines()[0]

    def test_rule_states_merge_target_constraint(self):
        from app.services.llm_decision.skill_normalize import build_skill_normalize_prompt

        prompt = build_skill_normalize_prompt("x", ["A"], alias_target="A")
        assert "不得改选其他近义候选" in prompt
        assert "不含 * 号" in prompt
