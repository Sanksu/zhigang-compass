"""回归测试：match_recommend 任务成功结果合并保留归属字段。

根因：worker 完成时 `task.result = {"match_id": ..., "top_n": ...}` 整体覆盖，
丢掉了入队时写入的 user_id。GET /match/task/{task_id} 的归属校验
（match.py match_task_status）读 task.result.user_id，缺失时对任何用户
都判"他人任务"→ 404 "匹配任务不存在或已过期"，任务成功却查不到结果。
"""

from app.workers.tasks import _complete_recommend_result


def test_keeps_enqueued_ownership_fields():
    """入队时 result 含 user_id/resume_id/top_n → 成功后这些字段必须保留。"""
    result = _complete_recommend_result(
        {"user_id": "u1", "resume_id": "r1", "top_n": 5},
        match_id="m1",
        top_n=3,
    )
    assert result["user_id"] == "u1"
    assert result["resume_id"] == "r1"
    assert result["match_id"] == "m1"
    assert result["top_n"] == 3


def test_appended_fields_override_previous_values():
    """match_id/top_n 以本次任务结果为准（覆盖入队时的占位 top_n）。"""
    result = _complete_recommend_result(
        {"user_id": "u1", "top_n": 10},
        match_id="m1",
        top_n=3,
    )
    assert result["top_n"] == 3
    assert result["match_id"] == "m1"


def test_none_prev_result_ok():
    """result 为 None（异常数据）不报错，仅含任务结果字段。"""
    result = _complete_recommend_result(None, match_id="m1", top_n=3)
    assert result == {"match_id": "m1", "top_n": 3}
