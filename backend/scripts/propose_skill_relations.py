"""技能关系 LLM 提议脚本（薄壳：核心实现已移入 app 包，供 ETL worker 共用）。

首窗口只生成 proposal（llm_decision_records status=proposal，risk_tier=R2），
审核通过前不写入图谱。候选来源=同一岗位 REQUIRES 技能对共现（top-N）；
硬门=节点存在/无自指/方向匹配 + 运行侧先修环判定（环候选直接 blocked）。

用法：
    uv run python scripts/propose_skill_relations.py --limit 40

红线：prompt 与方向语义属算法核心（services/llm_decision/skill_relation.py），
变更须张恺天 review。
"""

import argparse
import asyncio
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

# 核心实现（app 包内，worker 与本脚本共用）；同步 propose 为 asyncio.run 包装。
from app.services.llm_decision.propose_relations import (  # noqa: F401,E402
    DEFAULT_LIMIT,
    MIN_COOCCUR,
    fetch_relation_inputs,
    select_candidates,
)
from app.services.llm_decision.propose_relations import (  # noqa: E402
    propose as _propose_async,
)


def propose(limit: int = DEFAULT_LIMIT) -> dict:
    """同步入口（保持脚本/既有测试签名）；内部 asyncio.run 驱动 async 核心。"""
    return asyncio.run(_propose_async(limit=limit))


def main() -> None:
    parser = argparse.ArgumentParser(description="技能关系 LLM 提议（proposal 仅落决策记录）")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    args = parser.parse_args()
    summary = propose(limit=args.limit)
    print(summary)


if __name__ == "__main__":
    main()
