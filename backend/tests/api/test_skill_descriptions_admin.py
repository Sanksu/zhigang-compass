"""技能解释 backfill 单元测试（审查③：裸文本 LLM 输出最小校验）。

覆盖 _call_desc_llm 的 strip / 空拒绝 / 超长拒绝；FakeChain 注入，不做真实 LLM 调用。
"""

import pytest

from app.api.v1.admin_routes.skill_descriptions import _call_desc_llm
from app.services.extraction.llm_provider import LLMExtractionError


class _FakeChain:
    """记录 prompt 并回放固定文本的最小链替身。"""

    def __init__(self, reply):
        self.reply = reply
        self.prompts: list[str] = []

    def call_text_sync(self, prompt):
        self.prompts.append(prompt)
        return self.reply


def test_call_desc_llm_strips_reply():
    chain = _FakeChain("  用于搭建低代码应用的技能。  ")
    assert _call_desc_llm(chain, "低代码平台搭建") == "用于搭建低代码应用的技能。"
    assert len(chain.prompts) == 1
    assert "低代码平台搭建" in chain.prompts[0]


def test_call_desc_llm_rejects_blank_reply():
    chain = _FakeChain("   ")
    with pytest.raises(LLMExtractionError):
        _call_desc_llm(chain, "低代码平台搭建")


def test_call_desc_llm_rejects_none_reply():
    chain = _FakeChain(None)
    with pytest.raises(LLMExtractionError):
        _call_desc_llm(chain, "低代码平台搭建")


def test_call_desc_llm_rejects_oversized_reply():
    chain = _FakeChain("长" * 501)
    with pytest.raises(LLMExtractionError):
        _call_desc_llm(chain, "低代码平台搭建")
