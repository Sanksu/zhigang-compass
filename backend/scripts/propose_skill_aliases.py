"""技能别名回写 LLM 提议脚本（方案①：LLM 发现 → 人工审批 → 回写词典）。

复用 app.services.llm_decision.propose_normalization.propose_skill_alias 的 async
核心：批量扫未归一技能 → LLM merge 结论（别名→标准名）→ confidence ≥ 0.8 →
写 skill_aliases(pending) 供人工审批。approve 后再由 sync_dynamic_aliases 写
Neo4j（可选）/ normalize_skill 并查。

用法：
    uv run python scripts/propose_skill_aliases.py --limit 40

红线：别名/白名单扩容属算法核心（AGENTS.md §4.1），审批=人工、LLM 只建议；
standard_name 必须命中 known_standard_names（gate 守护）。
"""

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

# 核心实现（app 包内）：同步 propose 为 asyncio.run 包装。
from app.services.llm_decision.propose_normalization import (  # noqa: E402
    DEFAULT_LIMIT,
    _provider_of,
)

_CST = timezone(timedelta(hours=8))


def propose(limit: int = DEFAULT_LIMIT) -> dict:
    """同步入口（保持脚本/既有测试签名）；内部 asyncio.run 驱动 async 核心。"""
    from app.services.extraction.llm_provider import LLMConfigurationError, LLMProviderChain
    from app.services.llm_decision.propose_normalization import propose_skill_alias

    try:
        llm = LLMProviderChain()
    except LLMConfigurationError:
        return {"status": "skipped", "reason": "LLM 未配置"}

    provider, model = _provider_of(llm)
    run_date = datetime.now(_CST).strftime("%Y-%m-%d")
    # async 核心落库用独立事件循环（propose_skill_alias 内自己 async_session_factory）
    return asyncio.run(propose_skill_alias(llm, provider, model, run_date, limit))


def main() -> None:
    parser = argparse.ArgumentParser(description="技能别名回写提议（write skill_aliases pending）")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args()
    summary = propose(limit=args.limit)
    print(summary)


if __name__ == "__main__":
    main()
