# -*- coding: utf-8 -*-
"""批量技能分类提议脚本纯函数单测：候选筛选/并发编排/晋升分档/写回参数。"""

import pytest

from scripts.batch_skill_category_propose import (
    HIGH_CONFIDENCE,
    build_promotion,
    select_candidates,
    write_suggestions,
)


class _StubSession:
    def __init__(self):
        self.calls = []

    def run(self, query, **params):
        self.calls.append((query, params))
        return []


def _row(name, req, suggested=None):
    return {"name": name, "req_count": req, "suggested_category": suggested}


class TestSelectCandidates:
    def test_dedupes_by_name_keeping_highest_req_count(self):
        rows = [_row("JVM", 90), _row("JVM", 120), _row(".NET", 100)]
        picked = select_candidates(rows, refresh=False)
        assert [r["name"] for r in picked] == ["JVM", ".NET"]
        jvm = next(r for r in picked if r["name"] == "JVM")
        assert jvm["req_count"] == 120

    def test_skips_existing_suggestion_unless_refresh(self):
        rows = [_row("MLOps", 99), _row("JVM", 94, suggested="后端")]
        assert [r["name"] for r in select_candidates(rows, refresh=False)] == ["MLOps"]
        refreshed = select_candidates(rows, refresh=True)
        assert {r["name"] for r in refreshed} == {"MLOps", "JVM"}

    def test_blank_names_dropped_and_order_desc(self):
        rows = [_row("  ", 5), _row(None, 9), _row("B", 1), _row("A", 2)]
        assert [r["name"] for r in select_candidates(rows, refresh=False)] == ["A", "B"]


class TestProposeBatch:
    def test_success_failure_partition_and_sorting(self):
        from scripts.batch_skill_category_propose import propose_batch

        class _Result:
            def __init__(self, category, confidence, reason):
                self.category = category
                self.confidence = confidence
                self.reason = reason

        rows = [_row("A", 10), _row("B", 20), _row("C", 30)]

        def classify(name):
            if name == "B":
                raise ValueError("LLM 拒绝")  # 异常也不阻塞批次
            if name == "C":
                return None  # LLM 失败静默
            return _Result("前端", 0.9, "r")

        classified, failed = propose_batch(rows, classify)
        assert sorted(failed) == ["B", "C"]
        assert [c["name"] for c in classified] == ["A"]
        assert classified[0]["req_count"] == 10

    def test_sorted_by_req_count_desc_regardless_of_completion_order(self):
        import time

        from scripts.batch_skill_category_propose import propose_batch

        class _Result:
            category = "后端"
            confidence = 0.8
            reason = ""

        def classify(name):
            if name == "slow":
                time.sleep(0.05)
            return _Result()

        classified, _ = propose_batch(
            [_row("slow", 5), _row("fast", 50)], classify, workers=2,
        )
        assert [c["name"] for c in classified] == ["fast", "slow"]

    def test_single_failure_does_not_kill_batch(self):
        from scripts.batch_skill_category_propose import propose_batch

        def classify(name):
            raise ValueError("LLM down")

        classified, failed = propose_batch([_row("X", 3)], classify)
        assert classified == [] and failed == ["X"]


class TestBuildPromotion:
    def test_split_by_confidence_and_group_by_category(self):
        classified = [
            {"name": "a", "category": "前端", "confidence": 0.9, "req_count": 10},
            {"name": "b", "category": "前端", "confidence": HIGH_CONFIDENCE, "req_count": 8},
            {"name": "c", "category": "后端", "confidence": 0.4, "req_count": 6},
        ]
        promo = build_promotion(classified)
        assert [i["name"] for i in promo["high_confidence"]["前端"]] == ["a", "b"]
        assert list(promo["needs_review"].keys()) == ["后端"]

    def test_empty_input(self):
        assert build_promotion([]) == {"high_confidence": {}, "needs_review": {}}


class TestWriteSuggestions:
    def test_unwind_write_params(self):
        session = _StubSession()
        classified = [{"name": "A", "category": "前端", "confidence": 0.9,
                       "reason": "r", "req_count": 10}]
        write_suggestions(session, classified, "2026-08-24")
        assert len(session.calls) == 1
        _, params = session.calls[0]
        assert params["at"] == "2026-08-24"
        assert params["rows"] == classified

    def test_empty_noop(self):
        session = _StubSession()
        write_suggestions(session, [], "2026-08-24")
        assert session.calls == []


@pytest.mark.parametrize("conf", [0.0, 0.69, 0.7])
def test_threshold_boundary(conf):
    item = {"name": "x", "category": "测试", "confidence": conf, "req_count": 1}
    bucket = build_promotion([item])
    assert (conf >= HIGH_CONFIDENCE) is ("x" in
           [i["name"] for i in bucket["high_confidence"].get("测试", [])])
