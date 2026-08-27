"""批量决策独立超时上限测试（第六轮审查：批量超时违 90s 契约）。

历史缺陷：批量归一 timeout = 30×batch_size（N=20 时 600s/次、3 provider 链
最坏 1800s），偏离设计文档 §6.5「批量走独立 batch_timeout（60~90s/批）」。
锁定：批量调用传给 extract_structured 的单 provider 超时 ≤ 90s。
"""

from app.services.llm_decision.position_name import (
    BATCH_DECIDE_TIMEOUT_SECONDS as POSITION_BATCH_CAP,
    decide_position_name_batch,
)
from app.services.llm_decision.skill_normalize import (
    BATCH_DECIDE_TIMEOUT_SECONDS as SKILL_BATCH_CAP,
    SkillNormalizeBatch,
    SkillNormalizeDecision,
    decide_skill_normalize_batch,
)


class _CapturingLLM:
    """记录 extract_structured 收到的 timeout，按 chunk 长度回填合法批量结果。"""

    def __init__(self, result_factory, item_marker: str):
        self.result_factory = result_factory
        self.item_marker = item_marker
        self.timeouts: list[int | None] = []
        self._chunk_lens: list[int] = []

    def extract_structured(self, prompt, response_model, *, system_prompt=None,
                           timeout=None, **kw):
        self.timeouts.append(timeout)
        count = prompt.count(self.item_marker)
        self._chunk_lens.append(count)
        return self.result_factory(count)


class TestBatchTimeoutCap:
    def test_skill_batch_timeout_capped(self):
        def _make(count):
            return SkillNormalizeBatch(results=[
                SkillNormalizeDecision(action="keep", confidence=0.5)
                for _ in range(count)
            ])

        # 条目标记 "】技能名："——注意"候选标准技能名："不含该子串
        llm = _CapturingLLM(_make, item_marker="] 技能名：")
        names = [f"skill_{i}" for i in range(20)]  # N=20 旧口径 timeout=600
        results = decide_skill_normalize_batch(names, llm)
        assert len(results) == 20 and all(r is not None for r in results)
        assert llm.timeouts == [SKILL_BATCH_CAP]
        assert SKILL_BATCH_CAP <= 90  # 设计文档 §6.5 批量独立超时 60~90s/批

    def test_position_batch_timeout_capped(self):
        from app.services.llm_decision.position_name import (
            PositionNameBatch,
            PositionNameDecision,
        )

        def _make(count):
            return PositionNameBatch(results=[
                PositionNameDecision(keep_original=True, confidence=0.5)
                for _ in range(count)
            ])

        llm = _CapturingLLM(_make, item_marker="] 原始标题：")
        titles = [f"岗位{i}" for i in range(20)]
        results = decide_position_name_batch(
            titles, ["zhilian"] * 20, [["Python"]] * 20, [[]] * 20, llm,
        )
        assert len(results) == 20
        assert llm.timeouts == [POSITION_BATCH_CAP]
        assert POSITION_BATCH_CAP <= 90
