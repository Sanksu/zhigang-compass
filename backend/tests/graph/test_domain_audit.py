# -*- coding: utf-8 -*-
"""域成员资格 LLM 自审单元测试（PR-C）。"""

import pytest
from pydantic import ValidationError

from app.services.graph_algorithms.domain_audit import (
    MembershipAuditPlan,
    MembershipVerdict,
    audit_domain_membership,
    build_membership_audit_prompt,
    persist_membership_flags,
)


class TestBuildPrompt:
    def test_prompt_contains_sorted_domains_and_members(self):
        prompt = build_membership_audit_prompt({"后端开发": ["Go开发工程师", "Java开发工程师"], "算法": ["算法工程师"]})
        assert '"cluster": "算法"' in prompt
        assert "Go开发工程师" in prompt
        assert "内聚" in prompt

    def test_prompt_empty_domains(self):
        assert "[]" in build_membership_audit_prompt({})


class TestSchema:
    def test_verdict_defaults(self):
        v = MembershipVerdict(cluster="后端开发", coherent=True)
        assert v.suspicious == [] and v.reason == ""

    def test_reason_length_capped(self):
        with pytest.raises(ValidationError):
            MembershipVerdict(cluster="x", coherent=False, reason="长" * 201)

    def test_plan_roundtrip(self):
        plan = MembershipAuditPlan.model_validate_json(
            '{"verdicts": [{"cluster": "算法", "coherent": false,'
            ' "suspicious": ["Go开发工程师"], "reason": "r"}]}'
        )
        assert plan.verdicts[0].suspicious == ["Go开发工程师"]


class TestAuditDomainMembership:
    def test_llm_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.extraction.llm_provider.LLMProviderChain",
            lambda: (_ for _ in ()).throw(RuntimeError("llm down")),
        )
        assert audit_domain_membership({"算法": ["算法工程师"]}) == []

    def test_unknown_cluster_and_member_filtered(self, monkeypatch):
        class _FakeLLM:
            def extract_structured(self, prompt, schema, system_prompt="", timeout=0):
                return MembershipAuditPlan(verdicts=[
                    MembershipVerdict(cluster="算法", coherent=True, suspicious=[]),
                    MembershipVerdict(cluster="幽灵域", coherent=False, suspicious=[]),
                    MembershipVerdict(cluster="算法", coherent=True,
                                      suspicious=["不存在的岗", "算法工程师"]),
                ])

        monkeypatch.setattr(
            "app.services.extraction.llm_provider.LLMProviderChain", _FakeLLM,
        )
        verdicts = audit_domain_membership({"算法": ["算法工程师", "大模型算法工程师"]})
        assert len(verdicts) == 2  # 幽灵域被丢弃
        assert verdicts[1].suspicious == ["算法工程师"]  # 幽灵成员被剔除


class TestPersistMembershipFlags:
    def test_no_suspicious_no_records(self, monkeypatch):
        calls: list = []

        async def _fake_run(coro):
            calls.append(coro)
            return 0

        verdicts = [MembershipVerdict(cluster="算法", coherent=True, suspicious=[])]
        assert persist_membership_flags(verdicts, {"算法": ["算法工程师"]}) == 0
        assert calls == []

    def test_suspicious_persists_proposal_records(self, monkeypatch):
        persisted: list = []

        async def _fake_persist(record):
            persisted.append(record)

        async def _fake_dispose():
            pass

        monkeypatch.setattr("app.services.llm_decision.persist_record", _fake_persist)


        class _Engine:
            dispose = staticmethod(_fake_dispose)

        monkeypatch.setattr("app.core.database.engine", _Engine())

        verdicts = [MembershipVerdict(cluster="后端开发", coherent=False,
                                      suspicious=["Murex应用"], reason="测试")]
        n = persist_membership_flags(
            verdicts, {"后端开发": ["Java开发工程师", "Murex应用"]},
            provider="p", model="m",
        )
        assert n == 1
        record = persisted[0]
        assert record.domain == "cluster_membership"
        assert record.status == "proposal"
        assert record.risk_tier == "R2"
        assert record.structured_output["suspicious"] == ["Murex应用"]
        assert record.structured_output["action"] == "flag_membership"
