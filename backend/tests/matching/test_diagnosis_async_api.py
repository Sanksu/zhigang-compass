"""Async diagnosis API regression tests."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

from app.api.v1 import match as match_api


class _UnusedDb:
    async def scalar(self, *args, **kwargs):
        raise AssertionError("cached diagnosis must not query task status")


def test_cached_report_returns_success_without_enqueue():
    report = {
        "match_id": "m1",
        "overall_summary": "ok",
        "radar_analysis": "ok",
        "top_gaps": [],
        "path_analysis": "",
        "recommendations": [],
    }
    snapshot = {"gaps": [{"skill": "Python"}]}
    with (
        patch.object(
            match_api, "_load_match_result", new=AsyncMock(return_value=snapshot)
        ),
        patch.object(match_api, "redis_client", new=AsyncMock()) as redis_mock,
        patch.object(match_api, "enqueue", new=AsyncMock()) as enqueue_mock,
    ):
        redis_mock.get.return_value = json.dumps(report)
        response = asyncio.run(
            match_api.request_match_diagnosis(
                match_id="m1", db=_UnusedDb(), user={"sub": "u1"}
            )
        )

    assert response.data["status"] == "success"
    assert response.data["report"] == report
    enqueue_mock.assert_not_awaited()


def test_result_without_gaps_is_rejected_before_enqueue():
    with (
        patch.object(
            match_api, "_load_match_result", new=AsyncMock(return_value={"gaps": []})
        ),
        patch.object(match_api, "enqueue", new=AsyncMock()) as enqueue_mock,
    ):
        response = asyncio.run(
            match_api.request_match_diagnosis(
                match_id="m1", db=_UnusedDb(), user={"sub": "u1"}
            )
        )

    assert response.status_code == 422
    enqueue_mock.assert_not_awaited()
