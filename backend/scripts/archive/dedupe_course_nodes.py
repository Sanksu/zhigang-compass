"""Neo4j 课程节点归并（08-15 课程池治理：coursera 搜索弹窗重复节点）。

背景：coursera 爬虫从搜索页 xdpModal 采集的课程 URL 为
`/search?...xdpModal=course~<base64_ID>`（非规范 `/learn/<slug>`），
与规范采集的同一课程形成同 title 双节点（28 组 / 27 个重复节点），
导致课程级语义兜底（_semantic_match_course）返回成对重复课程。

判据（已实测 100% 覆盖，2026-08-15）：
- 同 source=coursera + 同 title 组内：
  - canonical：source_url 含 /learn/ 或 /specializations/（规范节点，31 个全有质量分）
  - xdp_modal：source_url 含 /search? 或 xdpModal（重复节点，27 个）
- 归并：xdpModal 的 LEARNABLE_VIA 边 MERGE 转移至规范节点 → DETACH DELETE
- 边界：icourse163 同 title 是不同学校的合法同名课（44 组）**不归并**；
  coursera 组内无规范节点（全 xdpModal）跳过报告人工处理
- course_raw（PG）行保留不动（原始数据仓库），不再被图谱引用

用法：
    uv run -- python scripts/dedupe_course_nodes.py            # dry-run 报告
    uv run -- python scripts/dedupe_course_nodes.py --apply    # 备份后归并
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.database import neo4j_driver

BACKUP_DIR = ROOT / "reports"


def classify(url: str) -> str:
    """coursera 课程 URL 分类：规范课程页 vs 搜索弹窗重复页。"""
    if not url:
        return "unknown"
    if "/search?" in url or "xdpModal" in url:
        return "xdp_modal"
    if "/learn/" in url or "/specializations/" in url:
        return "canonical"
    return "other"


def collect() -> tuple[list[dict], dict[str, list[dict]]]:
    """收集 coursera 全部课程节点 + (title → 节点) 分组。"""
    with neo4j_driver.session() as session:
        recs = session.run(
            """
            MATCH (c:Course) WHERE c.source = 'coursera'
            OPTIONAL MATCH (c)<-[:LEARNABLE_VIA]-(s:Skill)
            RETURN c.id AS id, c.source AS source, c.source_id AS source_id,
                   c.name AS name, c.source_url AS source_url,
                   count(s) AS edges
            """
        ).data()
    nodes = [
        {
            "id": r["id"], "source": r["source"], "source_id": r.get("source_id") or "",
            "name": r["name"], "url": r.get("source_url") or "", "edges": r["edges"],
        }
        for r in recs
    ]
    groups: dict[str, list[dict]] = defaultdict(list)
    for n in nodes:
        groups[n["name"]].append(n)
    return nodes, groups


def plan(groups: dict[str, list[dict]]) -> tuple[list[dict], list[dict], list[str]]:
    """归并计划：[(keep, dup)] 对列表 + 跳过组说明。"""
    pairs: list[dict] = []
    skipped: list[str] = []
    for name, members in groups.items():
        if len(members) < 2:
            continue
        canonical = [m for m in members if classify(m["url"]) == "canonical"]
        dupes = [m for m in members if classify(m["url"]) == "xdp_modal"]
        others = [m for m in members if classify(m["url"]) in ("unknown", "other")]
        if not dupes:
            continue  # 无 xdpModal 重复（icourse163 同名课等）
        if not canonical:
            skipped.append(f"{name}: 无规范节点，仅 xdpModal {len(members)} 个——人工处理")
            continue
        # 多规范节点取边数最多者保留（课程关联最完整）
        keep = max(canonical, key=lambda m: m["edges"])
        for d in dupes:
            pairs.append({"name": name, "keep": keep, "dup": d})
        for o in others:
            skipped.append(f"{name}: 未分类节点 {o['id']}（{o['url'][:60]}）——人工确认")
    return pairs, skipped


def apply_merge(pairs: list[dict]) -> list[dict]:
    """执行归并（事务内转移边 + 删除重复节点），返回备份记录。"""
    backup: list[dict] = []
    with neo4j_driver.session() as session:
        for p in pairs:
            dup, keep = p["dup"], p["keep"]
            # 边方向 Skill→Course（无属性，MERGE 自动去重）；同一事务内转移后删除
            session.run(
                """
                MATCH (s:Skill)-[r:LEARNABLE_VIA]->(dup:Course {id: $dup_id})
                MATCH (keep:Course {id: $keep_id})
                MERGE (s)-[:LEARNABLE_VIA]->(keep)
                WITH dup
                DETACH DELETE dup
                """,
                dup_id=dup["id"], keep_id=keep["id"],
            )
            backup.append({
                "kept_id": keep["id"], "kept_sid": keep["source_id"],
                "removed_id": dup["id"], "removed_sid": dup["source_id"],
                "name": p["name"], "url": dup["url"], "edges": dup["edges"],
            })
    return backup


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="实际归并（默认 dry-run）")
    args = parser.parse_args()

    nodes, groups = collect()
    pairs, skipped = plan(groups)

    print(f"coursera 课程节点 {len(nodes)}，重复组 {len(pairs) and len({p['name'] for p in pairs})}，"
          f"待归并重复节点 {len(pairs)}")
    for p in pairs:
        print(f"  归并: {p['name']}  xdpModal={p['dup']['id']}（{p['dup']['edges']} 边）"
              f"→ 规范={p['keep']['id']}（{p['keep']['edges']} 边）")
    for s in skipped:
        print(f"  [跳过] {s}")

    if not args.apply:
        print(f"\n[ dry-run ] 未执行。确认后加 --apply（备份至 {BACKUP_DIR}）")
        return 0

    backup = apply_merge(pairs)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = BACKUP_DIR / f"course_dedupe_backup_{stamp}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for rec in backup:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"\n已归并 {len(backup)} 个重复节点，备份: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
