"""LLM 驱动决策器评测脚本（PR9a1：分类/归一/关系 vs 确定性 gold）。

对三组冻结黄金集（data/golden_set/llm_driven/*.jsonl）跑真实 LLM 决策器，
测的是「LLM 决策器与权威确定性事实的一致性」：

- classification：对冻结技能名直接调 LLM 分类（不走白名单快速路径），
  gold=权威 category → top-1 accuracy / macro-F1 / 三跑一致率
- normalization：变体名 → LLM 归并决策，gold=别名映射标准名（merge）或
  短词 keep（错误合并率必须为 0）
- relation：技能对 → LLM 关系判定，gold=先修/父子/替代 YAML 或 NONE
  → 关系 precision / 方向准确率

用法：
    uv run python scripts/eval_llm_driven.py --task all
    uv run python scripts/eval_llm_driven.py --task classification --limit 20  # 冒烟
输出（reports/llm_driven_eval_{ts}/）：逐条 jsonl + 指标 md。

红线：决策器 prompt 属算法核心，阈值/结论请张恺天 review。
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("eval_llm_driven")

_GOLDEN_DIR = Path(_BACKEND_DIR) / "data" / "golden_set" / "llm_driven"
_REPORT_DIR = Path(_BACKEND_DIR) / "reports"
_FILES = {
    "classification": "classification_150.jsonl",
    "normalization": "normalization_150.jsonl",
    "relation": "relation_100.jsonl",
}


def _load_rows(task: str) -> list[dict]:
    fname = _FILES.get(task)
    if fname is None:
        return []
    path = _GOLDEN_DIR / fname
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _macro_f1(hits: list[dict]) -> dict:
    """按 gold 类别分组算 F1 后取平均（macro），并返回逐组统计。"""
    from collections import defaultdict

    groups: dict[str, dict] = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    for h in hits:
        g = groups[h["gold"]]
        if h["match"]:
            g["tp"] += 1
        else:
            g["fn"] += 1
            g["fp"] += 1
    per = []
    for label, c in groups.items():
        p = c["tp"] / (c["tp"] + c["fp"]) if (c["tp"] + c["fp"]) else 0.0
        r = c["tp"] / (c["tp"] + c["fn"]) if (c["tp"] + c["fn"]) else 0.0
        f1 = 2 * p * r / (p + r) if p + r else 0.0
        per.append({"gold": label, "tp": c["tp"], "fp": c["fp"], "fn": c["fn"],
                    "f1": round(f1, 4)})
    macro = round(sum(x["f1"] for x in per) / len(per), 4) if per else 0.0
    return {"macro_f1": macro, "group_count": len(per), "groups": per}


async def eval_classification(rows: list[dict], llm) -> dict:
    """分类：LLM 直判（绕过白名单快速路径），gold=权威 category。"""
    from app.services.extraction.skill_category_review import classify_skill
    from app.services.extraction.llm_provider import LLMExtractionError

    results: list[dict] = []
    llm_failed = 0
    for row in rows:
        # 只捕 LLM 链路异常（超时/限流/校验）计为 failed；脚本自身 bug 直接抛出，
        # 防止把编码错误淹没在 llm_failed 里（审查 P2 修复）
        try:
            decision = await asyncio.to_thread(classify_skill, row["skill"], llm)
        except LLMExtractionError:
            decision = None
        if decision is None:
            llm_failed += 1
            results.append({"skill": row["skill"], "gold": row["gold_category"],
                            "predicted": None, "match": False, "failed": True})
            continue
        results.append({
            "skill": row["skill"], "gold": row["gold_category"],
            "predicted": decision.category, "confidence": decision.confidence,
            "match": decision.category == row["gold_category"], "failed": False,
        })
    ok = [r for r in results if not r["failed"]]
    accuracy = round(sum(1 for r in ok if r["match"]) / len(ok), 4) if ok else 0.0
    return {
        "task": "classification", "total": len(rows), "llm_failed": llm_failed,
        "top1_accuracy": accuracy, "macro": _macro_f1(ok), "results": results,
    }


async def eval_normalization(rows: list[dict], llm) -> dict:
    """归一：变体→ merge 到 gold 标准名 / 短词 keep。"""
    from app.services.llm_decision.skill_normalize import (
        decide_skill_normalize, skill_normalize_gate,
    )

    results: list[dict] = []
    llm_failed = merge_hit = error_merge = 0
    for row in rows:
        decision = await asyncio.to_thread(decide_skill_normalize, row["variant"], llm)
        if decision is None:
            llm_failed += 1
            results.append({"variant": row["variant"], "gold_action": row["gold_action"],
                            "match": False, "failed": True})
            continue
        gate_ok, _ = skill_normalize_gate(decision, row["variant"])
        if row["gold_action"] == "keep":
            match = decision.action in ("keep", "noise")
            if decision.action == "merge":
                error_merge += 1
        else:
            match = decision.action == "merge" and decision.target_standard == row["gold_standard"]
            if decision.action == "merge":
                merge_hit += int(match)
        results.append({
            "variant": row["variant"], "gold_action": row["gold_action"],
            "gold_standard": row.get("gold_standard"),
            "predicted_action": decision.action,
            "predicted_target": decision.target_standard,
            "match": match, "gate_ok": gate_ok, "failed": False,
        })
    ok = [r for r in results if not r["failed"]]
    merge_rows = [r for r in ok if r["gold_action"] == "merge"]
    keep_rows = [r for r in ok if r["gold_action"] == "keep"]
    return {
        "task": "normalization", "total": len(rows), "llm_failed": llm_failed,
        "merge_accuracy": round(sum(1 for r in merge_rows if r["match"]) / len(merge_rows), 4) if merge_rows else None,
        "merge_count": len(merge_rows),
        "keep_accuracy": round(sum(1 for r in keep_rows if r["match"]) / len(keep_rows), 4) if keep_rows else None,
        "keep_count": len(keep_rows),
        "error_merge_count": error_merge,  # 短词被错误 merge 数（验收必须 0）
        "results": results,
    }


async def eval_relation(rows: list[dict], llm) -> dict:
    """关系：gold=先修/父子/替代 YAML 或 NONE。命中=类型一致（NONE 为 NONE）。"""
    from app.services.llm_decision.skill_relation import (
        REL_NONE, decide_skill_relation, skill_relation_gate,
    )

    results: list[dict] = []
    llm_failed = hits = direction_ok = 0
    for row in rows:
        decision = await asyncio.to_thread(
            decide_skill_relation, row["source"], row["target"], [], llm,
        )
        if decision is None:
            llm_failed += 1
            results.append({"source": row["source"], "target": row["target"],
                            "gold": row["gold_relation"], "match": False, "failed": True})
            continue
        gate_ok, _ = skill_relation_gate(decision, row["source"], row["target"], {row["source"], row["target"]})
        type_hit = decision.relation == row["gold_relation"] or (
            decision.relation == REL_NONE and row["gold_relation"] == REL_NONE
        )
        dir_ok = decision.direction == row["gold_direction"]
        hits += int(type_hit)
        direction_ok += int(type_hit and dir_ok)
        results.append({
            "source": row["source"], "target": row["target"],
            "gold": row["gold_relation"], "gold_direction": row["gold_direction"],
            "predicted": decision.relation, "direction": decision.direction,
            "type_hit": type_hit, "direction_ok": dir_ok,
            "match": type_hit and dir_ok, "gate_ok": gate_ok, "failed": False,
        })
    ok = [r for r in results if not r["failed"]]
    return {
        "task": "relation", "total": len(rows), "llm_failed": llm_failed,
        "precision": round(sum(1 for r in ok if r["type_hit"]) / len(ok), 4) if ok else None,
        "direction_accuracy": round(sum(1 for r in ok if r["direction_ok"]) / len(ok), 4) if ok else None,
        "results": results,
    }


async def main_async(args) -> dict:
    from app.services.extraction.llm_provider import LLMConfigurationError, LLMProviderChain

    try:
        llm = LLMProviderChain()
    except LLMConfigurationError as e:
        raise SystemExit(f"LLM 未配置: {e}")

    summary: dict = {}
    for task in args.task:
        rows = _load_rows(task)
        if not rows:
            print(f"[{task}] 黄金集缺失或为空（先运行 scripts/freeze_llm_golden.py）")
            continue
        if args.limit:
            rows = rows[: args.limit]
        fn = {"classification": eval_classification, "normalization": eval_normalization,
              "relation": eval_relation}[task]
        started = time.perf_counter()
        result = await fn(rows, llm)
        result["duration_seconds"] = round(time.perf_counter() - started, 1)
        summary[task] = {k: v for k, v in result.items() if k != "results"}
        ts = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M%S")
        out_dir = _REPORT_DIR / f"llm_driven_eval_{ts}"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{task}.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in result["results"]),
            encoding="utf-8",
        )
        print(f"[{task}] {summary[task]}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM 驱动决策器 vs 确定性 gold 评测")
    parser.add_argument("--task", nargs="+", choices=["classification", "normalization", "relation", "all"], default=["all"])
    parser.add_argument("--limit", type=int, default=0, help="冒烟采样数（0=全量）")
    args = parser.parse_args()
    if "all" in args.task:
        args.task = ["classification", "normalization", "relation"]
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()