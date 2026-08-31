# -*- coding: utf-8 -*-
"""域成员资格 LLM 自审（2026-08-31 岗位域治理 PR-C）。

动机：Go开发工程师 被错聚进生成式AI簇、TypeScript工程师 错配系统可靠性域，
均靠人肉看图发现——本模块把"语义内聚性抽检"变成域同步的常驻自检步骤：
每次域划分后一次批量 LLM 调用，对每个语义域的成员名单判定内聚性与可疑
成员；不内聚的域/可疑成员落 llm_decision_records（domain=cluster_membership，
status=proposal，R2 需人工审核），经管理后台决策审批页出列处置。

定位与边界：
- 只做检测与留痕，不自动改写图谱归属（批准/驳回均无副作用，驳回=误报
  关单）；修复走 pins 治理层或人工审核工具
- 任何失败（LLM 不可达/schema 违例/落库失败）只告警不阻塞域同步
- prompt 属算法红线，改动须张恺天 review
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import BaseModel, Field

from app.core.logging import setup_logging

logger = setup_logging("domain_audit")

_MEMBER_AUDIT_PROMPT = """你是招聘技能图谱的岗位 taxonomy 审核员。下面是岗位职能域划分结果，
每个域列出成员岗位名（full 表示该域全部成员）。逐域判定成员资格语义内聚性：

1. coherent：该域作为「职能域」是否语义内聚（成员属于同一类职能方向）
2. suspicious：与域职能方向不符、疑似被错误聚入的成员岗位名（须是 members
   中的原样字符串；没有则空数组）
3. reason：50 字内说明主要依据

判定从严：拿不准视为内聚（宁可漏报不可误报，误报会消耗人工审核）。"""

_AUDIT_SYSTEM_PROMPT = "你是严谨的岗位 taxonomy 审核员，严格按 JSON schema 输出。"


class MembershipVerdict(BaseModel):
    """单域裁决（幻觉防控第一道防线：schema 强校验，cluster 为对齐键）。"""

    cluster: str = Field(description="输入的域代表名，原样回传作为对齐键")
    coherent: bool = Field(description="该域是否语义内聚")
    suspicious: list[str] = Field(default_factory=list, description="疑似错配成员岗位名")
    reason: str = Field(default="", max_length=200, description="判定依据")


class MembershipAuditPlan(BaseModel):
    verdicts: list[MembershipVerdict] = Field(default_factory=list)


def build_membership_audit_prompt(domains: dict[str, list[str]]) -> str:
    """域成员表 → 审核 prompt（纯函数，供单测）。"""
    import json as _json

    payload = [
        {"cluster": name, "full": members}
        for name, members in sorted(domains.items())
    ]
    return _MEMBER_AUDIT_PROMPT + "\n\n" + _json.dumps(
        payload, ensure_ascii=False, indent=1,
    )


def audit_domain_membership(
    domains: dict[str, list[str]],
    timeout: int = 60,
) -> list[MembershipVerdict]:
    """批量 LLM 内聚性裁决；任何失败返回 []（调用方记日志，不阻塞同步）。"""
    import json as _json

    from app.services.extraction.llm_invocation import invocation_scope
    from app.services.extraction.llm_provider import (
        LLMConfigurationError,
        LLMExtractionError,
        LLMProviderChain,
    )

    if not domains:
        return []
    prompt = build_membership_audit_prompt(domains)
    try:
        llm = LLMProviderChain()
        with invocation_scope("domain_membership_audit"):
            plan = llm.extract_structured(
                prompt, MembershipAuditPlan,
                system_prompt=_AUDIT_SYSTEM_PROMPT,
                timeout=timeout,
            )
    except Exception as e:  # noqa: BLE001 — 自审失败不阻塞域同步，只留日志
        logger.warning("[membership_audit] LLM 自审失败，跳过: %s", e)
        return []

    known = set(domains)
    member_flat = {m for members in domains.values() for m in members}
    valid: list[MembershipVerdict] = []
    for v in plan.verdicts:
        if v.cluster not in known:
            logger.warning("[membership_audit] 未知域键 %r 丢弃", v.cluster)
            continue
        dropped = [m for m in v.suspicious if m not in member_flat]
        if dropped:
            logger.warning("[membership_audit] %s 可疑成员不在域表，剔除: %s", v.cluster, dropped)
            v.suspicious = [m for m in v.suspicious if m in member_flat]
        valid.append(v)
    return valid


def persist_membership_flags(
    verdicts: list[MembershipVerdict],
    domains: dict[str, list[str]],
    provider: str = "",
    model: str = "",
) -> int:
    """可疑成员/不内聚域落 llm_decision_records（proposal/R2，best-effort）。

    单次 asyncio.run 批量落库并清理连接池（Windows 脚本上下文跨 loop
    陈旧池坑）；返回成功落库条数。只在有可疑项时落记录，内聚域不产生
    审批噪音。
    """
    import asyncio

    flagged = [
        (v, domains.get(v.cluster, []))
        for v in verdicts if (not v.coherent or v.suspicious)
    ]
    if not flagged:
        logger.info("[membership_audit] 全部 %s 域内聚，无待审项", len(verdicts))
        return 0

    from app.core.database import engine
    from app.services.llm_decision import (
        DOMAIN_CLUSTER_MEMBERSHIP,
        STATUS_PROPOSAL,
        TIER_R2,
        build_record,
    )

    run_date = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    records = []
    for v, members in flagged:
        records.append(build_record(
            domain=DOMAIN_CLUSTER_MEMBERSHIP,
            entity_type="cluster",
            entity_id=v.cluster,
            run_id=f"membership_audit:{run_date}",
            input_hash=hashlib.sha256(
                f"{v.cluster}\n{sorted(members)}".encode("utf-8"),
            ).hexdigest(),
            evidence_refs=[{"member_count": len(members), "members": members}],
            provider=provider, model=model,
            structured_output={
                "action": "flag_membership",
                "coherent": v.coherent,
                "suspicious": v.suspicious,
                "reason": v.reason,
            },
            confidence=None,
            gate_result="pass",
            risk_tier=TIER_R2,
            status=STATUS_PROPOSAL,
        ))

    async def _persist_all() -> int:
        # 丢弃跨事件循环的陈旧连接池（脚本上下文多次 asyncio.run 的 Windows 坑）
        await engine.dispose()
        from app.services.llm_decision import persist_record
        ok = 0
        for record in records:
            try:
                await persist_record(record)
                ok += 1
            except Exception as e:  # noqa: BLE001 — best-effort，单条失败不影响其余
                logger.warning("[membership_audit] 记录落库失败: %s", e)
        await engine.dispose()
        return ok

    try:
        ok = asyncio.run(_persist_all())
    except Exception as e:  # noqa: BLE001
        logger.warning("[membership_audit] 批量落库失败: %s", e)
        return 0
    logger.info("[membership_audit] 落库 %s/%s 条待审记录", ok, len(records))
    return ok


def run_membership_audit(domains: dict[str, list[str]]) -> int:
    """自审入口：LLM 裁决 → 可疑项落库（域同步脚本调用）。返回落库条数。"""
    if not domains:
        return 0
    verdicts = audit_domain_membership(domains)
    if not verdicts:
        return 0
    provider = model = ""
    try:
        from app.services.extraction.llm_provider import LLMProviderChain
        primary = (LLMProviderChain()._providers or [{}])[0]
        provider = str(primary.get("name") or "")
        model = str(primary.get("model") or "")
    except Exception:  # noqa: BLE001 — 元信息缺失不影响落库
        pass
    return persist_membership_flags(verdicts, domains, provider=provider, model=model)
