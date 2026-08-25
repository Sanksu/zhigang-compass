"""名称归一 LLM 提议脚本（薄壳：核心实现已移入 app 包，供 ETL worker 共用）。

首窗口只生成 proposal（llm_decision_records status=proposal，risk_tier=R2），
审核通过前不写入图谱（rename/merge 属 R2 高风险图变异）。区别于 shadow 影子
（status=shadow，只落档不生效）：本脚本把「规则无法稳定裁决」的候选提为人工
审核池，由 admin 在决策页 approve 后，scripts/sync_dynamic_normalization.py
幂等应用到 Neo4j。

用法：
    uv run python scripts/propose_name_normalization.py --limit 40 [--domain skill|position]

红线：prompt 与硬门属算法核心（services/llm_decision/position_name.py /
skill_normalize.py），变更须张恺天 review。本脚本仅编排，不改判定逻辑。
"""

import argparse
import asyncio
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

# 核心实现（app 包内，worker 与本脚本共用）；同步 propose 为 asyncio.run 包装。
from app.services.llm_decision.propose_normalization import (  # noqa: F401,E402
    DEFAULT_LIMIT,
    RISK_TIER,
    _input_hash,
)
from app.services.llm_decision.propose_normalization import (  # noqa: E402
    propose as _propose_async,
)


def propose(limit: int = DEFAULT_LIMIT, domain: str = "all") -> dict:
    """同步入口（保持脚本/既有测试签名）；内部 asyncio.run 驱动 async 核心。"""
    return asyncio.run(_propose_async(limit=limit, domain=domain))


def main() -> None:
    parser = argparse.ArgumentParser(description="名称归一 LLM 提议（proposal 仅落决策记录）")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--domain", choices=["all", "position", "skill"], default="all")
    args = parser.parse_args()
    summary = propose(limit=args.limit, domain=args.domain)
    print(summary)


if __name__ == "__main__":
    main()
