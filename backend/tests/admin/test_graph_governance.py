# -*- coding: utf-8 -*-
"""图谱域治理管理接口测试：group_domains/assemble_summary 纯函数 + 409 锁。"""

import pytest
from fastapi import HTTPException

from app.api.v1.admin_routes import graph_governance as mod


def _rows():
    return [
        {"name": "前端开发工程师", "dom": "dom_1", "dname": "前端开发", "freq": 597, "source": "backbone"},
        {"name": "React前端开发工程师", "dom": "dom_1", "dname": "前端开发", "freq": 6, "source": "pin"},
        {"name": "数据分析师", "dom": "dom_2", "dname": "数据分析", "freq": 281, "source": "backbone"},
        {"name": "CT技师", "dom": "dom_general", "dname": "通用与其他岗位", "freq": 2, "source": None},
    ]


class TestGroupDomains:
    def test_general_last_and_members_freq_desc(self):
        domains = mod.group_domains(_rows())
        assert [d["domain_id"] for d in domains] == ["dom_1", "dom_2", "dom_general"]
        front = domains[0]
        assert front["member_count"] == 2
        assert [m["name"] for m in front["members"]] == ["前端开发工程师", "React前端开发工程师"]
        assert front["members"][0]["source"] == "backbone"
        assert front["source_counts"] == {"backbone": 1, "pin": 1}
        assert front["is_general"] is False
        assert domains[-1]["is_general"] is True

    def test_member_truncation_and_null_domain_fallback(self):
        rows = [
            {"name": f"岗{i}", "dom": "dom_9", "dname": "杂", "freq": i}
            for i in range(15)
        ] + [{"name": "无域岗", "dom": None, "dname": None, "freq": 1}]
        domains = mod.group_domains(rows, top_members=12)
        big = next(d for d in domains if d["domain_id"] == "dom_9")
        assert big["member_count"] == 15 and len(big["members"]) == 12
        assert big["members"][0]["name"] == "岗14"
        orphan = next(d for d in domains if d["domain_id"] == mod.GENERAL_DOMAIN_ID)
        assert [m["name"] for m in orphan["members"]] == ["无域岗"]


class TestAssembleSummary:
    def test_shape_and_counts(self):
        domains = mod.group_domains(_rows())
        out = mod.assemble_summary(
            domains,
            benchmark={"evaluated": 72, "strict_accuracy": 0.764, "pairwise_f1": 0.589, "failures": []},
            membership_pending=3,
            resync_running=False,
            last_resync="2026-08-31T19:00:00+08:00",
        )
        assert out["positions"] == 4
        assert out["semantic_domains"] == 2
        assert out["general_count"] == 1
        assert out["membership_pending"] == 3
        assert out["benchmark"]["strict_accuracy"] == pytest.approx(0.764)


class TestResyncLock:
    def test_running_state_returns_409(self, monkeypatch):
        mod._resync_state["running"] = True
        try:
            with pytest.raises(HTTPException) as ei:
                __import__("asyncio").run(mod.graph_governance_resync(
                    background_tasks=None, current_user={},
                ))
            assert ei.value.status_code == 409
        finally:
            mod._resync_state["running"] = False
