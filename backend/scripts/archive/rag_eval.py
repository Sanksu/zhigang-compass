"""RAG 接地检索质量评测（2026-08-13，评审 P0-1）。

对图谱高频岗位 + 候选池岗位跑 search_authoritative，评估：
- recall@1：top-1 是否命中"期望大典职业"（别名映射表反推 ground truth）
- recall@5：top-5 内命中
- 覆盖率：top-1 命中任意大典职业的比例（接地兜底有效性）
- 失败样例输出（便于人工核查与回归）

期望映射（ground truth）来自 grounding 别名桥接映射（JD 岗位名 → 大典职业）。

用法：
    uv run python scripts/rag_eval.py                    # 全量评测
    uv run python scripts/rag_eval.py --top 10           # 仅前 N 个高频岗位
    uv run python scripts/rag_eval.py --json reports/rag_eval.json
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("rag_eval")

# 期望映射：JD 岗位名 → 大典职业名（别名桥接 ground truth，与 grounding 映射一致）
EXPECTED = {
    "前端开发工程师": "计算机软件工程技术人员",
    "Java开发工程师": "计算机软件工程技术人员",
    "后端开发工程师": "计算机软件工程技术人员",
    "全栈工程师": "计算机软件工程技术人员",
    "Python开发工程师": "计算机软件工程技术人员",
    "C++开发工程师": "计算机软件工程技术人员",
    "移动开发工程师": "计算机软件工程技术人员",
    "测试工程师": "计算机软件工程技术人员",
    "大模型算法工程师": "人工智能工程技术人员",
    "算法工程师": "人工智能工程技术人员",
    "机器视觉算法工程师": "人工智能工程技术人员",
    "语音算法工程师": "人工智能工程技术人员",
    "机器人算法工程师": "人工智能工程技术人员",
    "自动驾驶算法工程师": "人工智能工程技术人员",
    "数据分析师": "数据分析处理工程技术人员",
    "数据科学家": "数据分析处理工程技术人员",
    "大数据开发工程师": "大数据工程技术人员",
    "DevOps工程师": "云计算工程技术人员",
    "网络安全工程师": "信息安全工程技术人员",
    "嵌入式开发工程师": "嵌入式系统设计工程技术人员",
    "运维工程师": "信息系统运行维护工程技术人员",
    "数据库管理员": "信息系统运行维护工程技术人员",
    "产品经理": "数字化管理师",
    "项目经理": "信息系统分析工程技术人员",
    "网络工程师": "信息系统运行维护工程技术人员",
}


def _top_positions(limit: int) -> list[str]:
    """图谱高频岗位 + 候选池岗位（去重）。"""
    from app.core.database import neo4j_driver

    names: list[str] = []
    with neo4j_driver.session() as s:
        rows = s.run(
            "MATCH (p:Position) RETURN p.name AS n ORDER BY coalesce(p.freq, 0) DESC LIMIT $limit",
            limit=limit * 2,
        ).data()
        names = [r["n"] for r in rows]
    return names


async def evaluate(top: int) -> dict:
    from app.core.database import async_session_factory, neo4j_driver
    from app.services.discovery import grounding

    # 评测必须走真实检索路径：禁用 grounding 缓存，避免缓存掩盖回归/污染指标
    grounding._CACHE_ENABLED = False
    search_authoritative = grounding.search_authoritative

    positions = _top_positions(top)
    # 只评测有期望映射的岗位（ground truth 已知）
    eval_set = [p for p in positions if p in EXPECTED]
    if not eval_set:
        # 图谱岗位不足时用期望映射全集
        eval_set = list(EXPECTED)

    r1 = r5 = covered = 0
    failures: list[dict] = []
    async with async_session_factory() as db:
        for pos in eval_set:
            hits = await search_authoritative(pos, db=db, neo4j=neo4j_driver, limit=5)
            names = [h.get("name") for h in hits]
            expected = EXPECTED.get(pos)
            hit1 = bool(names and names[0] == expected)
            hit5 = expected in names
            hit_any = bool(names)
            r1 += int(hit1)
            r5 += int(hit5)
            covered += int(hit_any)
            if not hit1:
                failures.append({"position": pos, "expected": expected, "top1": names[0] if names else None, "top5": names})

    n = len(eval_set)
    result = {
        "total": n,
        "recall_at_1": r1 / n if n else 0.0,
        "recall_at_5": r5 / n if n else 0.0,
        "coverage": covered / n if n else 0.0,
        "failures": failures,
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG 接地检索质量评测")
    parser.add_argument("--top", type=int, default=30, help="取图谱前 N 个高频岗位（默认 30）")
    parser.add_argument("--json", type=Path, default=None, help="输出 JSON 报告路径")
    args = parser.parse_args()

    result = asyncio.run(evaluate(args.top))
    n = result["total"]
    print(f"[RAG 接地评测] {n} 个岗位（期望映射 ground truth）")
    print(f"  recall@1 = {result['recall_at_1']:.3f}（{int(result['recall_at_1']*n)}/{n}）")
    print(f"  recall@5 = {result['recall_at_5']:.3f}（{int(result['recall_at_5']*n)}/{n}）")
    print(f"  覆盖率（top-1 命中任意大典职业）= {result['coverage']:.3f}")
    if result["failures"]:
        print(f"  失败样例（top-1 未命中期望）: {len(result['failures'])}")
        for f in result["failures"][:8]:
            print(f"    {f['position']} → 期望 {f['expected']} | 实际 top1={f['top1']} top5={f['top5']}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("报告已写入 %s", args.json)


if __name__ == "__main__":
    main()
