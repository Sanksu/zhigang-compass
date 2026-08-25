"""LLM 驱动决策器评测脚本（PR9a1：分类/归一/关系 vs 确定性 gold）。

对三组冻结黄金集（data/golden_set/llm_driven/*.jsonl）跑真实 LLM 决策器，
测的是「LLM 决策器与权威确定性事实的一致性」：

- classification：对冻结技能名直接调 LLM 分类（不走白名单快速路径），
  gold=权威 category → top-1 accuracy / macro-F1 / 三跑一致率
- normalization：变体名 → LLM 归并决策，gold=别名映射标准名（merge）或
  短词 keep（错误合并率必须为 0）；口径=alias 权威遵循率（①重定义，
  见 eval_normalization docstring），非生产归一 accuracy
- relation：技能对 → LLM 关系判定，gold=先修/父子/替代 YAML 或 NONE
  → 关系 precision / 方向准确率
- position：岗位名 → LLM 归一决策，gold=canonical/is_new/keep_original
  → canonical_accuracy / is_new_accuracy / keep_original_accuracy /
  gate_blocked_count / error_merge_count（DRAFT 行 gold_* 为机器建议，
  仅参考，正式指标须人工转正后读取）

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
    "position": "position_normalization_draft.jsonl",
}

# position 任务读取任意 position_normalization* 黄金/草稿文件（优先草稿主文件）。
_POSITION_GLOB_PREFIX = "position_normalization"


def _get_file_for(task: str) -> str | None:
    """任务 → 数据文件名。

    position 任务可路由到任意 position_normalization* 文件（冻结金标准或草稿），
    依 _POSITION_GLOB_PREFIX 前缀通配；其余任务固定映射。
    """
    if task == "position":
        # 优先草稿主文件，其次已人工转正的同前缀文件（若存在）。
        for cand in (
            "position_normalization_draft.jsonl",
            "position_normalization_gold.jsonl",
            "position_normalization_frozen.jsonl",
        ):
            if (_GOLDEN_DIR / cand).exists():
                return cand
        return None
    return _FILES.get(task)


def _load_rows(task: str) -> list[dict]:
    fname = _get_file_for(task)
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


# 分类同域簇（校准 r3 副指标）：近邻可辩口径的容差分组。
# 官方验收口径保持严格 top-1；容差口径供张恺天拍板时数据支撑
# （r2 实证 12/18 错误为同域近邻互判，如 SQL→数据库 vs 编程语言）。
_CATEGORY_FAMILY: dict[str, str] = {
    "编程语言": "基础技术", "数据库": "基础技术", "网络/协议": "基础技术",
    "计算机基础": "基础技术", "操作系统": "基础技术",
    "AI/机器学习": "数据与AI", "大数据": "数据与AI", "数据分析/商业": "数据与AI",
    "智能驾驶/机器人": "数据与AI",
    "前端": "应用开发", "后端": "应用开发", "移动/桌面": "应用开发",
    "云原生/DevOps": "应用开发", "测试": "应用开发", "游戏/数字孪生": "应用开发",
    "消息/中间件": "应用开发",
    "安全": "安全与运维", "硬件/芯片": "硬件与嵌入式", "音视频": "应用开发",
    "工程协作": "软技能与管理", "软技能": "软技能与管理",
}


def _family(category: str) -> str:
    return _CATEGORY_FAMILY.get(category, "其他")


async def eval_classification(rows: list[dict], llm) -> dict:
    """分类：LLM 直判（绕过白名单快速路径），gold=权威 category。

    top1_accuracy=严格口径（官方验收）；tolerance_accuracy=同域簇容差
    （同簇互判不误判，供口径拍板数据支撑，不作为放行依据）。
    """
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
        tolerance = _family(decision.category) == _family(row["gold_category"])
        results.append({
            "skill": row["skill"], "gold": row["gold_category"],
            "predicted": decision.category, "confidence": decision.confidence,
            "match": decision.category == row["gold_category"],
            "tolerance": tolerance, "failed": False,
        })
    ok = [r for r in results if not r["failed"]]
    accuracy = round(sum(1 for r in ok if r["match"]) / len(ok), 4) if ok else 0.0
    tolerance_accuracy = round(sum(1 for r in ok if r["tolerance"]) / len(ok), 4) if ok else 0.0
    return {
        "task": "classification", "total": len(rows), "llm_failed": llm_failed,
        "top1_accuracy": accuracy, "tolerance_accuracy": tolerance_accuracy,
        "macro": _macro_f1(ok), "results": results,
    }


async def eval_normalization(rows: list[dict], llm) -> dict:
    """归一：变体→ merge 到 gold 标准名 / 短词 keep。

    口径（2026-08-25 拍板 ① 重定义）：本任务测「alias 权威遵循率」——
    评测集全部为 alias 域变体（slice=alias），生产中由确定性快速路径直解
    不进 LLM；r7 起 prompt 对 alias 落点标注 * 约束 merge 目标，指标语义
    = LLM 对权威落点的遵循率（参考线 0.90），不再作为生产归一 accuracy
    的验收口径（生产口径=快速路径直解+LLM 兜底，另行汇报）。
    """
    from app.services.llm_decision.skill_normalize import (
        SKILL_BATCH_SIZE, decide_skill_normalize_batch, skill_normalize_gate,
    )

    results: list[dict] = []
    llm_failed = merge_hit = error_merge = gate_saved = 0
    # 08-25 提速：批量技能归一（N=SKILL_BATCH_SIZE，16×提速）；gate/gold 逐条比对不变。
    variants = [(r.get("variant") or "").strip() for r in rows if (r.get("variant") or "").strip()]
    if not variants:
        ok: list[dict] = []
        merge_rows = keep_rows = []
    else:
        decisions = await asyncio.to_thread(
            decide_skill_normalize_batch, variants, llm, batch_size=SKILL_BATCH_SIZE,
        )
        for row, decision in zip(rows, decisions):
            if decision is None:
                llm_failed += 1
                results.append({"variant": row["variant"], "gold_action": row["gold_action"],
                                "match": False, "failed": True})
                continue
            gate_ok, _ = skill_normalize_gate(decision, row["variant"])
            if row["gold_action"] == "keep":
                match = decision.action in ("keep", "noise")
                if decision.action == "merge":
                    if gate_ok:
                        error_merge += 1  # 硬门未拦的真实错误合并（验收必须 0）
                    else:
                        # 硬门拦截（如 AIGC→AIGC 同义反复）：生产零风险，单列不计错误
                        gate_saved += 1
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
        "error_merge_count": error_merge,  # 硬门未拦的真实错误合并（验收必须 0）
        "gate_saved_count": gate_saved,  # 硬门拦截的无效 merge（生产零风险）
        "results": results,
    }


async def eval_position_normalization(rows: list[dict], llm) -> dict:
    """岗位名归一：LLM 决策器 vs gold_canonical / gold_is_new / gold_keep_original。

    gold 为 final/jd_golden 或 DRAFT 集（annotation_status=draft_auto = 机器建议，
    仅作参考，不计入正式指标）。指标（生产口径按评分目的区分为两类）：

    - canonical_accuracy   ：LLM canonical == gold_canonical（变体键容忍空白/全角/大小写）
    - is_new_accuracy      ：LLM is_new == gold_is_new
    - keep_original_accuracy：LLM keep_original == gold_keep_original
    - gate_blocked_count   ：hard gate（position_name_gate）拦截条数（防幻觉）
    - error_merge_count    ：gate 未拦、但与 gold 不一致的"错归并"条数

    核心思路：评分必须过 gate——gate 拦截（防幻觉长名/空名/自创名）视为防御成功，
    不误记为"错误"；只有 gate 通过但仍与 gold 不一致才记 error_merge_count。
    """
    from app.services.llm_decision.position_name import (
        POSITION_BATCH_SIZE, decide_position_name_batch, position_name_gate,
    )

    results: list[dict] = []
    llm_failed = canonical_hit = is_new_hit = keep_hit = gate_blocked = error_merge = 0
    human_rows = 0

    def _vk(s: str) -> str:
        return "".join(s.split()).casefold() if s else ""

    # 08-25 提速：批量决策（N=POSITION_BATCH_SIZE），一次 LLM 调用多条（16×提速）。
    # 逐条比对逻辑不变——批量仅替换 LLM 调用，gate/gold 比对逐条独立。
    active_rows = [r for r in rows if (r.get("raw_title") or r.get("position_name") or "").strip()]
    titles = [(r.get("raw_title") or r.get("position_name") or "").strip() for r in active_rows]
    sources = [(r.get("source") or "") for r in active_rows]
    skills_l = [(r.get("skills") or []) for r in active_rows]
    cands_l = [(r.get("candidates") or []) for r in active_rows]
    decisions = await asyncio.to_thread(
        decide_position_name_batch, titles, sources, skills_l, cands_l, llm,
        batch_size=POSITION_BATCH_SIZE,
    )

    for row, decision in zip(active_rows, decisions):
        raw = (row.get("raw_title") or row.get("position_name") or "").strip()
        if row.get("annotation_status") == "human":
            human_rows += 1
        if decision is None:
            llm_failed += 1
            results.append({"raw_title": raw, "failed": True, "match": False})
            continue
        gate_ok, gate_reason = position_name_gate(decision, raw, row.get("candidates") or [])
        if not gate_ok:
            gate_blocked += 1
            results.append({
                "raw_title": raw, "gate_ok": False, "gate_reason": gate_reason,
                "predicted_canonical": decision.canonical_name,
                "predicted_is_new": decision.is_new,
                "predicted_keep_original": decision.keep_original,
                "failed": False, "match": False,
            })
            continue

        # 采纳 A（08-25）：reject_excluded 行（含"实习"=招聘形态不入图）岗位维不评
        # canonical/is_new/keep（生产 normalize_position_name 返回空），仅记录 excluded。
        # 技能维另评（该 JD 技能仍有效，见 _load_gold_jsonl）。
        if row.get("resolution") == "reject_excluded":
            results.append({
                "raw_title": raw, "gate_ok": True, "excluded": True,
                "gold_canonical": "", "gold_is_new": False, "gold_keep_original": True,
                "predicted_canonical": decision.canonical_name,
                "predicted_is_new": decision.is_new, "predicted_keep_original": decision.keep_original,
                "canonical_ok": True, "is_new_ok": True, "keep_ok": True,
                "match": True, "failed": False,
            })
            continue

        # gold 取值（DRAFT 行 gold_* 为机器建议初值，仅参考；human 行为最终金标准）
        gold_canon = row.get("gold_canonical") or ""
        gold_is_new = bool(row.get("gold_is_new"))
        gold_keep = bool(row.get("gold_keep_original"))

        canonical_ok = (
            _vk(decision.canonical_name) == _vk(gold_canon)
            if not (gold_keep or decision.keep_original or gold_is_new or decision.is_new)
            else True
        )
        # 若 gold 与原始标题一致（keep_original），canonical 名义一致由 keep 决定
        is_new_ok = decision.is_new == gold_is_new
        keep_ok = decision.keep_original == gold_keep
        canonical_hit += int(canonical_ok)
        is_new_hit += int(is_new_ok)
        keep_hit += int(keep_ok)

        # 错误归并：keep_original 语义冲突或 canonical 语义错过 gold（gate 未拦）
        mismatch = (not canonical_ok) or (not is_new_ok) or (not keep_ok)
        if mismatch:
            error_merge += 1
        results.append({
            "raw_title": raw, "gate_ok": True,
            "gold_canonical": gold_canon, "gold_is_new": gold_is_new,
            "gold_keep_original": gold_keep,
            "predicted_canonical": decision.canonical_name,
            "predicted_is_new": decision.is_new,
            "predicted_keep_original": decision.keep_original,
            "canonical_ok": canonical_ok, "is_new_ok": is_new_ok,
            "keep_ok": keep_ok, "match": not mismatch,
            "failed": False,
        })

    ok = [r for r in results if not r["failed"] and r.get("gate_ok")]
    return {
        "task": "position_normalization", "total": len(rows),
        "human_confirmed_rows": human_rows,
        "llm_failed": llm_failed,
        "canonical_accuracy": round(sum(1 for r in ok if r["canonical_ok"]) / len(ok), 4) if ok else None,
        "is_new_accuracy": round(sum(1 for r in ok if r["is_new_ok"]) / len(ok), 4) if ok else None,
        "keep_original_accuracy": round(sum(1 for r in ok if r["keep_ok"]) / len(ok), 4) if ok else None,
        "gate_blocked_count": gate_blocked,
        "error_merge_count": error_merge,
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
              "relation": eval_relation, "position": eval_position_normalization}[task]
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
    parser.add_argument("--task", nargs="+", choices=["classification", "normalization", "relation", "position", "all"], default=["all"])
    parser.add_argument("--limit", type=int, default=0, help="冒烟采样数（0=全量）")
    args = parser.parse_args()
    if "all" in args.task:
        args.task = ["classification", "normalization", "relation", "position"]
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()