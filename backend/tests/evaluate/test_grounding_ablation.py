"""Grounding 防线消融基座回归测试（CI 可复跑，无 LLM/DB 依赖）。

固化 run_grounding_ablation 金标准判定：幻觉案例按预期档位被拦截、
漏网案例不被拦截、控制组零误拦截。任何 NLI 阈值/信号逻辑回归都会在此暴露。
"""

import asyncio

from run_grounding_ablation import (
    CONTROL_CASES,
    HALLUCINATION_CASES,
    _run_case,
    _stage_of,
)


def _run_all() -> list:
    """同步跑全部金标准案例（伪 LLM 确定性，无副作用）。"""

    async def _go():
        out = []
        for cid, premise, draft, expected in HALLUCINATION_CASES:
            res, calls, nli = await _run_case(premise, draft)
            out.append({
                "id": cid, "kind": "hallucination", "expected": expected,
                "actual": _stage_of(res, calls, nli), "intercepted": res.nli_contradicted,
            })
        for cid, premise, draft in CONTROL_CASES:
            res, calls, nli = await _run_case(premise, draft)
            out.append({
                "id": cid, "kind": "control",
                "intercepted": res.nli_contradicted,
            })
        return out

    return asyncio.run(_go())


def test_hallucination_cases_match_expected_stage():
    results = _run_all()
    hall = [r for r in results if r["kind"] == "hallucination"]
    assert len(hall) == len(HALLUCINATION_CASES)
    for r in hall:
        assert r["actual"] == r["expected"], (
            f"{r['id']}: expected={r['expected']} actual={r['actual']} "
            f"(NLI 档位判定回归，请检查 nli_guard 信号/阈值)"
        )


def test_pass_cases_not_intercepted():
    results = _run_all()
    miss = [r for r in results if r["kind"] == "hallucination" and r["expected"] == "pass"]
    assert miss, "金标准需包含至少一条漏网案例以展示防线边界"
    for r in miss:
        assert not r["intercepted"], f"{r['id']} 应漏网（无对立断言，NLI 不拦截）"


def test_control_cases_zero_false_interception():
    results = _run_all()
    ctrl = [r for r in results if r["kind"] == "control"]
    assert ctrl
    for r in ctrl:
        assert not r["intercepted"], (
            f"控制组 {r['id']} 被误拦截——忠实草案不应触发软门控"
        )


def test_interception_rate_above_half():
    """幻觉拦截率应稳定过半（金标准整体守卫强度下限）。"""
    results = _run_all()
    hall = [r for r in results if r["kind"] == "hallucination"]
    intercepted = sum(1 for r in hall if r["intercepted"])
    rate = intercepted / len(hall)
    assert rate > 0.5, f"幻觉拦截率 {rate:.1%} 过低，防线疑似失效"
