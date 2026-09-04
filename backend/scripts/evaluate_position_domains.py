# -*- coding: utf-8 -*-
"""岗位域划分评测（共成员关系基准，离线可复现）。

基准口径：data/golden_set/position_domain_eval.jsonl 中每行断言
「position 应与 same_domain_as 锚点岗同属一个语义域」（expect=domain）
或「position 应落入通用与其他域」（expect=general，弃权断言）。断言与
LLM 生成的域名解耦——锚点岗选 freq≥10 高频稳定岗，域算法重构/调参不失效。

指标：
- strict_accuracy：行级严格通过率（domain 行要求全部锚点同域且非通用域；
  general 行要求实际落通用域）
- pairwise P/R/F1：domain 行按锚点关系并查集聚类后，与实际划分的共成员
  关系对级 precision/recall/F1（划分质量指标，不受簇 ID 平移影响）

评测对象是当前 Neo4j 图谱的 Position.domain_id 划分（由
sync_position_domains.py 产出），报告写 reports/，默认只报告不做门禁。

用法（cwd=backend）：
    python scripts/evaluate_position_domains.py
    python scripts/evaluate_position_domains.py --min-accuracy 0.8   # 门禁模式
    python scripts/evaluate_position_domains.py --golden <其他基准文件>
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.database import neo4j_driver
from app.core.logging import setup_logging

logger = setup_logging("evaluate_position_domains")

_GOLDEN = ROOT / "data" / "golden_set" / "position_domain_eval.jsonl"
GENERAL_DOMAIN_ID = "dom_general"


def _load_golden(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        row = json.loads(line)
        if any(k.startswith("_") for k in row):
            continue  # _meta 等元数据行
        if row.get("expect") == "domain":
            if not row.get("same_domain_as"):
                raise ValueError(f"基准第 {line_no} 行 expect=domain 缺 same_domain_as")
        elif row.get("expect") != "general":
            raise ValueError(f"基准第 {line_no} 行 expect 必须为 domain/general")
        rows.append(row)
    return rows


def _load_graph() -> dict[str, str]:
    """公开状态岗位 → domain_id（无域按通用域兜底，与 sync 口径一致）。"""
    with neo4j_driver.session() as session:
        rows = session.run(
            """
            MATCH (p:Position) WHERE p.status IN ['active','emerging','stable','declining']
            RETURN p.name AS name, p.domain_id AS dom
            """
        ).data()
    return {r["name"]: (r["dom"] or GENERAL_DOMAIN_ID) for r in rows if r.get("name")}


def _evaluate(rows: list[dict], graph: dict[str, str]) -> tuple[dict, list[dict]]:
    results: list[dict] = []
    for row in rows:
        pos = row["position"]
        if pos not in graph:
            results.append({**row, "verdict": "missing", "detail": "不在图谱公开岗位中"})
            continue
        actual = graph[pos]
        if row["expect"] == "general":
            ok = actual == GENERAL_DOMAIN_ID
            detail = f"实际域={actual}"
        else:
            anchors = row["same_domain_as"]
            missing = [a for a in anchors if a not in graph]
            if missing:
                results.append({**row, "verdict": "missing", "detail": f"锚点不在图谱: {missing}"})
                continue
            hits = [a for a in anchors if graph[a] == actual and actual != GENERAL_DOMAIN_ID]
            ok = len(hits) == len(anchors)
            detail = f"锚点命中 {len(hits)}/{len(anchors)}"
        results.append({**row, "verdict": "pass" if ok else "fail", "detail": detail})

    evaluated = [r for r in results if r["verdict"] != "missing"]
    dom_rows = [r for r in evaluated if r["expect"] == "domain"]
    gen_rows = [r for r in evaluated if r["expect"] == "general"]

    def _rate(sub: list[dict]) -> float | None:
        return round(sum(1 for r in sub if r["verdict"] == "pass") / len(sub), 4) if sub else None

    stats = {
        "labeled": len(rows),
        "evaluated": len(evaluated),
        "missing": len(results) - len(evaluated),
        "strict_accuracy": _rate(evaluated),
        "domain_accuracy": _rate(dom_rows),
        "general_accuracy": _rate(gen_rows),
        "pairwise": _pairwise_prf(evaluated, graph),
        "failures": [
            {"position": r["position"], "expect": r["expect"], "detail": r["detail"],
             "note": r.get("note", "")}
            for r in evaluated if r["verdict"] == "fail"
        ],
    }
    return stats, results


def _pairwise_prf(rows: list[dict], graph: dict[str, str]) -> dict:
    """domain 行（position+锚点并查集）vs 实际划分的共成员对级 P/R/F1。"""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for row in rows:
        if row["expect"] != "domain":
            continue
        members = [row["position"], *row["same_domain_as"]]
        for m in members[1:]:
            parent[find(m)] = find(members[0])

    groups: dict[str, list[str]] = {}
    for name in parent:
        groups.setdefault(find(name), []).append(name)
    expected: set[tuple[str, str]] = set()
    for members in groups.values():
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                expected.add((min(a, b), max(a, b)))

    in_domain = {n: d for n, d in graph.items() if d != GENERAL_DOMAIN_ID}
    by_domain: dict[str, list[str]] = {}
    for n, d in in_domain.items():
        by_domain.setdefault(d, []).append(n)
    predicted: set[tuple[str, str]] = set()
    for members in by_domain.values():
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                predicted.add((min(a, b), max(a, b)))

    inter = len(expected & predicted)
    p = inter / len(predicted) if predicted else 0.0
    r = inter / len(expected) if expected else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4),
            "expected_pairs": len(expected), "predicted_pairs": len(predicted),
            "note": "pairwise 全图对比（预测侧含未标注岗位），F1 主要看趋势"}


def _report(stats: dict, results: list[dict], out: Path) -> None:
    def _pct(v: float | None) -> str:
        return f"{v:.1%}" if v is not None else "n/a"

    lines = [
        "# 岗位域划分评测报告",
        "",
        f"- 时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "- 基准：data/golden_set/position_domain_eval.jsonl（共成员关系口径，DRAFT 待人工复核）",
        f"- 行级严格通过率：**{_pct(stats['strict_accuracy'])}**"
        f"（{stats['evaluated']} 可评 / {stats['missing']} missing）",
        f"- 域内行准确率：{_pct(stats['domain_accuracy'])}｜弃权行准确率：{_pct(stats['general_accuracy'])}",
        f"- 共成员对级：P={stats['pairwise']['precision']:.3f} R={stats['pairwise']['recall']:.3f} "
        f"F1={stats['pairwise']['f1']:.3f}",
        "",
        "## 未通过项",
        "",
    ]
    if stats["failures"]:
        lines += ["| 岗位 | 断言 | 实际 | 备注 |", "|---|---|---|---|"]
        for f in stats["failures"]:
            lines.append(f"| {f['position']} | {f['expect']} | {f['detail']} | {f['note']} |")
    else:
        lines.append("（无）")
    out.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="岗位域划分评测（共成员关系基准）")
    parser.add_argument("--golden", type=Path, default=_GOLDEN)
    parser.add_argument("--min-accuracy", type=float, default=None,
                        help="门禁模式：strict_accuracy 低于该值退出码 1")
    args = parser.parse_args(argv)

    rows = _load_golden(args.golden)
    graph = _load_graph()
    stats, results = _evaluate(rows, graph)

    date = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = ROOT / "reports" / f"position_domain_eval_{date}.md"
    _report(stats, results, out)
    logger.info("基准评测：%s 可评，严格通过率 %.1f%%，pairwise F1 %.3f",
                stats["evaluated"], (stats["strict_accuracy"] or 0) * 100,
                stats["pairwise"]["f1"])
    for f in stats["failures"]:
        logger.warning("  FAIL %s（expect=%s）%s", f["position"], f["expect"], f["detail"])
    logger.info("报告: %s", out)
    if args.min_accuracy is not None and (stats["strict_accuracy"] or 0) < args.min_accuracy:
        logger.error("低于门禁 %.2f", args.min_accuracy)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
