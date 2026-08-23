"""岗位名 LLM 审查 M1 实验脚本（《岗位名LLM审查设计方案》§6 阶段一）。

从图谱抽取低频非标准岗位名样本 → LLM 预审 → 产出人工核对 CSV：
    uv run python scripts/experiment_position_review.py --limit 50
    uv run python scripts/experiment_position_review.py --dry-run   # 只列候选不调 LLM

产出 reports/position_review_m1_{date}.csv（人工判定/备注两列留空供填写）
与同名 .md 摘要（分类分布统计）。通过标准：分类 ≥90% / 修正 ≥80% / 误杀 0；
达标后经管理后台开启 position_review_enabled 灰度。

只读实验：不写图谱、不写规则库、不改任何配置。
"""

import argparse
import csv
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

_CST = timezone(timedelta(hours=8))
_REPORT_DIR = Path(__file__).resolve().parents[1] / "reports"

_CSV_HEADERS = [
    "岗位名", "LLM_valid", "LLM_category", "LLM_standard_name", "LLM_reason",
    "人工判定(正确/误杀/修正错)", "备注",
]


def _fetch_low_ref_positions(limit_pool: int = 200) -> list[dict]:
    """图谱低引用岗位（候选池，引用升序）——与 dict_guard 脏岗位同款查询。"""
    from app.core.database import neo4j_driver

    query = (
        "MATCH (p:Position) "
        "OPTIONAL MATCH (p)<-[r:REQUIRES]-() "
        "WITH p, count(r) AS req_count "
        "WHERE req_count <= 1 "
        "RETURN p.name AS name, req_count, p.first_seen AS first_seen "
        "ORDER BY req_count ASC, p.first_seen DESC LIMIT $limit"
    )
    with neo4j_driver.session() as session:
        return [dict(record) for record in session.run(query, limit=limit_pool)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=50, help="抽样上限（默认 50）")
    parser.add_argument("--pool", type=int, default=200, help="候选池扫描上限")
    parser.add_argument("--dry-run", action="store_true", help="只列候选，不调 LLM")
    args = parser.parse_args()

    from app.services.extraction.position_review import (
        review_position_name,
        select_experiment_candidates,
    )

    rows = _fetch_low_ref_positions(limit_pool=args.pool)
    candidates = select_experiment_candidates(rows, limit=args.limit)
    print(f"候选池 {len(rows)} 行 → 符合抽样门 {len(candidates)} 个")
    for name in candidates:
        print(f"  - {name}")
    if args.dry_run:
        return 0
    if not candidates:
        print("无候选，结束。")
        return 0

    from app.services.extraction.llm_provider import LLMProviderChain

    try:
        llm = LLMProviderChain()
    except Exception as e:
        print(f"LLM 未配置，无法预审：{e}")
        return 1

    date_tag = datetime.now(_CST).strftime("%Y-%m-%d")
    out_csv = _REPORT_DIR / f"position_review_m1_{date_tag}.csv"
    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    categories: Counter = Counter()
    valid_count = 0

    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(_CSV_HEADERS)
        for i, name in enumerate(candidates, start=1):
            result = review_position_name(name, skills=[], llm=llm)
            if result is None:
                writer.writerow([name, "(LLM 失败降级)", "", "", "", "", ""])
                print(f"[{i}/{len(candidates)}] {name} → LLM 失败跳过")
                continue
            categories[result.category] += 1
            valid_count += int(result.valid)
            writer.writerow([
                name, result.valid, result.category,
                result.standard_name or "", result.reason, "", "",
            ])
            print(
                f"[{i}/{len(candidates)}] {name} → valid={result.valid} "
                f"{result.category} {result.standard_name or ''}"
            )

    reviewed = sum(categories.values())
    summary = (
        f"# 岗位名 LLM 审查 M1 实验（{date_tag}）\n\n"
        f"- 样本：{reviewed}/{len(candidates)}（LLM 失败降级不计入分类统计）\n"
        f"- valid=true：{valid_count}；invalid：{reviewed - valid_count}\n"
        f"- 分类分布：{dict(categories)}\n\n"
        f"## 通过标准（§6）\n\n"
        f"- 分类准确率 ≥ 90%\n- 修正映射准确率 ≥ 80%\n- 误杀率 = 0\n\n"
        f"请在 {out_csv.name} 中填写「人工判定」列后交算法岗汇总。\n"
    )
    out_md = _REPORT_DIR / f"position_review_m1_{date_tag}.md"
    out_md.write_text(summary, encoding="utf-8")
    print(f"\nCSV: {out_csv}\n摘要: {out_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
