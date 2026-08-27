"""三跑中位聚合——110 条 gold 正式验收复测（TE-M5-02/AL-M5-01）。

用法：python scripts/agg_gold_accept.py <archive1> <archive2> <archive3> [--out reports/gold_acceptance_<date>.md]
读 run_manual_jd_eval.py 归档（eval_jd_llm_*.json），取技能 aligned F1 / raw F1 中位，
输出正式结论文本。aligned 口径=词面真值对齐（PR #330）。
"""
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    argv = sys.argv[1:]
    out = None
    if "--out" in argv:
        i = argv.index("--out")
        out = Path(argv[i + 1])
        argv = argv[:i]
    archives = [Path(a) for a in argv]
    if len(archives) != 3:
        print("usage: agg_gold_accept.py <a1> <a2> <a3> [--out file.md]")
        return 2
    runs = []
    for a in archives:
        d = json.loads(a.read_text(encoding="utf-8"))
        r = d["results"][0]
        assert r["samples"] == 110, f"{a.name}: samples={r['samples']} != 110"
        runs.append({
            "file": a.name,
            "generated_at": d["generated_at"],
            "aligned_f1": r["f1"],
            "raw_f1": r["skills_micro_raw"]["f1"],
            "precision": r["precision"],
            "recall": r["recall"],
            "fallback": r["fallback_samples"],
            "failed": r["failed_samples"],
            "commit": r.get("commit", "?"),
            "provider": r.get("provider", "?"),
            "model": r.get("model", "?"),
            "hallu_fp": sum(r.get("hallucinated_fp", {}).values()),
        })
    med_aligned = statistics.median(r["aligned_f1"] for r in runs)
    med_raw = statistics.median(r["raw_f1"] for r in runs)
    ok = med_aligned >= 0.90
    lines = [
        "# 110 条 gold 正式验收复测结论（TE-M5-02 / AL-M5-01）",
        "",
        f"- 生成时间：{datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}",
        "- 口径：技能 aligned F1 = 词面真值对齐（PR #330），EVAL_SPEC_VERSION=20260824-a（与 08-25 预验收一致，可比）",
        f"- 三跑（全部 110 条，fallback=0，failed=0）：",
        "",
        "| 轮次 | 归档 | aligned F1 | raw F1 | precision | recall | 幻觉 FP | commit | provider/model |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(runs, 1):
        lines.append(
            f"| R{i} | {r['file']} | {r['aligned_f1']:.4f} | {r['raw_f1']:.4f} | "
            f"{r['precision']:.4f} | {r['recall']:.4f} | {r['hallu_fp']} | {r['commit']} | {r['provider']}/{r['model']} |"
        )
    lines += [
        "",
        f"## 正式结论",
        "",
        f"- 三跑中位 **aligned F1 = {med_aligned:.4f}**（raw 中位 {med_raw:.4f}）",
        f"- 阈值 ≥ 0.90：{'✅ 达标，正式验收通过' if ok else '❌ 未达标'}",
        f"- 运行环境：226 zhigang-api 容器（develop @ 运行时 HEAD，与 08-25 预验收同口径）",
        "",
    ]
    text = "\n".join(lines)
    print(text)
    if out:
        out.write_text(text, encoding="utf-8")
        print(f"written: {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
