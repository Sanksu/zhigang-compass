"""岗位重复对治理脚本（2026-08-16 重复岗位对治理）。

背景：normalize_position_name 只有关键词/前后缀规则，无字符级清洗——
空格/全角差异（"CMDB 发现" vs "CMDB发现"）与语义近似名（"AI 数据科学机器人教练"
vs "AI数据科学与机器人教练"）会分裂成多个图谱节点/候选池行。本脚本三来源盘点
（Neo4j Position / PG discovery_candidates / jd_raw 抽取快照）并分阶段治理：

阶段 A — 字符级变体自动合并：_variant_key 分组（语义别名命中先重映射组键），
            组内 normalize_position_name 一致（非空）或别名命中才自动合并；
            别名命中且组内单成员（存量遗留）→ 改名统一。
阶段 B — 语义近似对提议：SBERT 相似度 ≥ 0.9（排除字符级变体对）输出复核清单
            （含双方来源与相似度），人工确认后写入 dictionary._POSITION_ALIAS
            并重建 _POSITION_ALIAS_BY_VARIANT，再重跑 A 生效。

合并/改名规则（--apply）：
- 主节点取 HAS_EVIDENCE 边数最多者（次之 freq、再次字典序）
- 重连 REQUIRES/HAS_EVIDENCE（ON CREATE SET 复制属性），SET 规范名
  （归一化一致输出 / 别名值），DETACH DELETE 重复节点
- 跳过 legacy（08-16 基线待决策）/rejected（保留审计痕迹）状态岗位，
  整组报告人工待办
- PG 候选行联动：行组无 rejected 时保留 state 优先级最高（或与规范名同名）
  行，evidence_refs 并集后删除其余行，行名统一为规范名（position_name 唯一，
  组内规范名不会与组外行冲突——组键即变体键）
- 绝不因归一化空串删除节点（cleanup_graph.merge_positions 空族误删教训）

备份：reports/position_duplicates_{stage}_{date}.jsonl（dry-run 也写）。
用法（cwd=backend）：
    python -m scripts.position_duplicate_cleanup                 # dry-run 报告
    python -m scripts.position_duplicate_cleanup --apply         # 执行合并/改名
    python -m scripts.position_duplicate_cleanup --stage A       # 只跑指定阶段
    python -m scripts.position_duplicate_cleanup --reaggregate   # 合并后重跑聚合
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from app.core.database import async_session_factory, engine, neo4j_driver
from app.core.logging import setup_logging
from app.models.business import DiscoveryCandidate
from app.models.raw import JDRaw
from app.services.extraction.dictionary import (
    _POSITION_ALIAS_BY_VARIANT,
    _variant_key,
    normalize_position_name,
)

logger = setup_logging("position_duplicate_cleanup")

# 语义对提议阈值（技能 SIMILAR_TO 用 0.85；岗位名更短、差异更敏感，取 0.9）
SIMILAR_THRESHOLD = 0.90

# 候选行状态优先级（行合并保留最高者）
_STATE_PRIORITY = {"stable": 5, "emerging": 4, "declining": 3,
                   "archived": 2, "candidate": 1, "rejected": 0}

# 图谱节点跳过合并的状态：legacy 待决策（08-16 基线遗留）、rejected 保留审计痕迹
_SKIP_STATUSES = ("legacy", "rejected")


def _group_key(name: str) -> str:
    """分组键：变体键 + 语义别名重映射（别名命中的名归入规范名的变体键组）。"""
    canonical = _POSITION_ALIAS_BY_VARIANT.get(_variant_key(name), name)
    return _variant_key(canonical)


def _backup(rows: list[dict], name: str) -> Path:
    """备份（dry-run 也写，便于人工复核清单）。"""
    date = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = ROOT / "reports" / f"position_duplicates_{name}_{date}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    logger.info("  备份: %s（%s 条）", path, len(rows))
    return path


def _load_graph() -> list[dict]:
    """Neo4j Position 全量（含 HAS_EVIDENCE 边数，供主节点选择）。"""
    with neo4j_driver.session() as session:
        rows = session.run(
            "MATCH (p:Position) OPTIONAL MATCH (p)-[h:HAS_EVIDENCE]->(:Evidence) "
            "RETURN p.name AS name, p.id AS id, p.status AS status, p.freq AS freq, "
            "count(h) AS ev_count"
        ).data()
    return [
        {
            "name": r["name"], "id": r["id"],
            "status": r.get("status") or "active",
            "freq": r.get("freq") or 0, "ev_count": r.get("ev_count") or 0,
        }
        for r in rows if r.get("name")
    ]


async def _load_pg() -> tuple[list[dict], set[str]]:
    """PG 候选池行 + jd_raw 抽取岗位名（去重）。"""
    async with async_session_factory() as s:
        cands = (await s.scalars(select(DiscoveryCandidate))).all()
        raw_snaps = (await s.scalars(select(JDRaw.snapshot))).all()
    candidates = [
        {
            "id": c.id, "name": c.position_name, "state": c.state,
            "evidence_refs": list(c.evidence_refs or []),
        }
        for c in cands
    ]
    jd_names: set[str] = set()
    for snap in raw_snaps:
        ext = (snap or {}).get("extraction") or {}
        n = (ext.get("position_name") or "").strip()
        if n:
            jd_names.add(n)
    # 关闭池连接：跨 asyncio.run 复用会触发 "Event loop is closed"（Windows
    # Proactor 坑），--reaggregate 等独立 asyncio.run 需要干净连接池
    await engine.dispose()
    return candidates, jd_names


def _merge_graph(session, primary_id: str, dup_ids: list[str], std: str) -> None:
    """重连 REQUIRES/HAS_EVIDENCE → SET 规范名 → 删重复节点（merge_positions 逻辑）。"""
    for dup in dup_ids:
        session.run(
            """
            MATCH (d:Position {id: $dup})-[r:REQUIRES]->(t)
            WITH d, t, r
            MATCH (p:Position {id: $primary})
            MERGE (p)-[r2:REQUIRES]->(t)
            ON CREATE SET r2 = properties(r)
            WITH d, r
            DELETE r
            """,
            primary=primary_id, dup=dup,
        )
        session.run(
            """
            MATCH (d:Position {id: $dup})-[r:HAS_EVIDENCE]->(e)
            WITH d, e, r
            MATCH (p:Position {id: $primary})
            MERGE (p)-[:HAS_EVIDENCE]->(e)
            WITH d, r
            DELETE r
            """,
            primary=primary_id, dup=dup,
        )
        session.run("MATCH (d:Position {id: $dup}) DETACH DELETE d", dup=dup)
    session.run(
        "MATCH (p:Position {id: $primary}) SET p.name = $std",
        primary=primary_id, std=std,
    )


async def _merge_candidate_rows(row_plans: list[dict], apply: bool) -> int:
    """PG 候选行联动：保留行并 evidence_refs、删重复行、行名统一。"""
    if not row_plans:
        return 0
    if not apply:
        return len(row_plans)
    async with async_session_factory() as s:
        for rp in row_plans:
            keep = await s.get(DiscoveryCandidate, rp["keep"])
            if keep:
                keep.evidence_refs = rp["refs"]
                if keep.position_name != rp["std"]:
                    keep.position_name = rp["std"]
            for row_id in rp["deletes"]:
                obj = await s.get(DiscoveryCandidate, row_id)
                if obj:
                    await s.delete(obj)
        await s.commit()
    return len(row_plans)


def stage_a(apply: bool, positions: list[dict], candidates: list[dict], jd_names: set[str]) -> int:
    """阶段 A：字符级变体/语义别名合并（dry-run 也备份计划）。

    positions/candidates/jd_names 由 main 一次性加载（asyncio.run 只能调一次，
    二次调用会复用已关闭事件循环的池连接——Windows Proactor 坑）。
    """
    logger.info("阶段 A — 字符级变体/语义别名合并")
    node_groups: dict[str, list[dict]] = defaultdict(list)
    for p in positions:
        node_groups[_group_key(p["name"])].append(p)
    row_groups: dict[str, list[dict]] = defaultdict(list)
    for c in candidates:
        row_groups[_group_key(c["name"])].append(c)

    plans: list[dict] = []
    for gk, members in node_groups.items():
        alias_std = next(
            (_POSITION_ALIAS_BY_VARIANT[vk] for m in members
             if (vk := _variant_key(m["name"])) in _POSITION_ALIAS_BY_VARIANT),
            None,
        )
        if len(members) == 1:
            # 单成员组：仅别名命中且名不同 → 改名统一（存量遗留），
            # 不动规则归一化改名（那是历史治理脚本的职责）
            m = members[0]
            if alias_std and m["name"] != alias_std:
                plans.append({
                    "group": gk, "std": alias_std, "action": "rename",
                    "primary": m["id"], "dups": [], "members": [m],
                    "skip": None,
                })
            continue
        skip = next(
            (m["name"] for m in members if m["status"] in _SKIP_STATUSES), None
        )
        if skip:
            plans.append({
                "group": gk, "std": "", "action": "skip",
                "primary": None, "dups": [], "members": members, "skip": skip,
            })
            continue
        if alias_std:
            std = alias_std
        else:
            stds = {normalize_position_name(m["name"]) for m in members}
            if len(stds) == 1 and "" not in stds:
                std = stds.pop()
            else:
                plans.append({
                    "group": gk, "std": "", "action": "no_std",
                    "primary": None, "dups": [], "members": members,
                    "skip": "归一化不一致或空串（碎片，不自动合并）",
                })
                continue
        members.sort(key=lambda m: (-m["ev_count"], -m["freq"], m["name"]))
        primary, dups = members[0], members[1:]
        plans.append({
            "group": gk, "std": std, "action": "merge",
            "primary": primary["id"], "dups": [d["id"] for d in dups],
            "members": members, "skip": None,
        })

    # jd_raw 抽取名提示：与图谱同组（未来 ETL 将归一入组）
    graph_keys = set(node_groups)
    jd_hint = sorted(
        n for n in jd_names if _group_key(n) in graph_keys and n not in
        {m["name"] for members in node_groups.values() for m in members}
    )
    if jd_hint:
        logger.info("  jd_raw 抽取名与图谱同组（ETL 后将归并）: %s", jd_hint[:10])

    stats = defaultdict(int)
    for p in plans:
        stats[p["action"]] += 1
    logger.info("  计划: %s", dict(stats))
    if not plans:
        logger.info("  无合并/改名候选")
        return 0
    _backup(plans, "stageA")
    if not apply:
        logger.info("  (dry-run，--apply 才执行)")
        return len(plans)

    with neo4j_driver.session() as session:
        for p in plans:
            if p["action"] == "merge":
                _merge_graph(session, p["primary"], p["dups"], p["std"])
            elif p["action"] == "rename":
                session.run(
                    "MATCH (p:Position {id: $id}) SET p.name = $std",
                    id=p["primary"], std=p["std"],
                )
    # PG 候选行联动（仅 merge/rename 组；行组无 rejected 才处理）
    row_plans = []
    for p in plans:
        if p["action"] not in ("merge", "rename"):
            continue
        rows = row_groups.get(p["group"]) or []
        if not rows:
            continue
        if any(r["state"] == "rejected" for r in rows):
            continue
        if len(rows) == 1:
            if rows[0]["name"] != p["std"]:
                row_plans.append({
                    "keep": rows[0]["id"], "deletes": [],
                    "refs": rows[0]["evidence_refs"], "std": p["std"],
                })
            continue
        keep = next((r for r in rows if r["name"] == p["std"]), None)
        if keep is None:
            keep = max(rows, key=lambda r: _STATE_PRIORITY.get(r["state"], 0))
        refs = sorted({ref for r in rows for ref in r["evidence_refs"]})
        row_plans.append({
            "keep": keep["id"], "deletes": [r["id"] for r in rows if r["id"] != keep["id"]],
            "refs": refs, "std": p["std"],
        })
    n_rows = asyncio.run(_merge_candidate_rows(row_plans, apply))
    logger.info("  候选行联动: %s 组%s", n_rows, "" if apply else "（dry-run）")
    return len(plans)


def stage_b(positions: list[dict], candidates: list[dict], jd_names: set[str]) -> int:
    """阶段 B：语义近似对提议（SBERT sim ≥ 阈值），输出复核清单。"""
    logger.info("阶段 B — 语义近似对提议（sim ≥ %.2f）", SIMILAR_THRESHOLD)
    from app.services.matching.semantic import SkillEmbedder

    graph_names = sorted({p["name"] for p in positions} | {c["name"] for c in candidates})
    jd_only = sorted(n for n in jd_names if n not in graph_names)
    logger.info("  图内/候选名 %s 个，jd_raw 独有名 %s 个", len(graph_names), len(jd_only))

    embedder = SkillEmbedder.get()
    embedder.warm(graph_names + jd_only)

    seen: set[tuple[str, str]] = set()
    pairs: list[dict] = []

    def _propose(a: str, b: str, a_src: str, b_src: str) -> None:
        key = (a, b) if a < b else (b, a)
        if key in seen:
            return
        seen.add(key)
        if _variant_key(a) == _variant_key(b):
            return  # 字符级变体，A 阶段覆盖
        sim = embedder.similarity(a, b)
        if sim >= SIMILAR_THRESHOLD:
            pairs.append({"a": key[0], "b": key[1], "sim": round(sim, 3),
                          "a_source": a_src if a == key[0] else b_src,
                          "b_source": b_src if b == key[0] else a_src,
                          "suggested": ""})

    for i, a in enumerate(graph_names):
        for b in graph_names[i + 1:]:
            _propose(a, b, "graph/candidate", "graph/candidate")
    for a in jd_only:
        for b in graph_names:
            _propose(a, b, "jd_raw", "graph/candidate")

    pairs.sort(key=lambda x: -x["sim"])
    logger.info("  语义近似对: %s 对", len(pairs))
    for p in pairs[:20]:
        logger.info("    %.3f  %s | %s", p["sim"], p["a"], p["b"])
    if not pairs:
        return 0
    _backup(pairs, "stageB")
    logger.info("  复核清单: 确认后写入 dictionary._POSITION_ALIAS（键=变体名，值=规范名）"
                "并重建 _POSITION_ALIAS_BY_VARIANT，再跑 --apply 合并")
    return len(pairs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="岗位重复对治理（变体合并/语义提议）")
    parser.add_argument("--apply", action="store_true", help="执行合并/改名（默认 dry-run 只报告+备份）")
    parser.add_argument("--stage", choices=["A", "B"], help="只跑指定阶段（默认全部）")
    parser.add_argument("--reaggregate", action="store_true",
                        help="合并后重跑岗位聚合（build_aggregates/write_aggregates）")
    args = parser.parse_args(argv)

    logger.info("=" * 60)
    logger.info("岗位重复对治理 %s", "[执行]" if args.apply else "[dry-run]")
    logger.info("=" * 60)

    total = 0
    # PG 数据只加载一次（asyncio.run 二次调用会复用已关闭事件循环的连接）
    positions = _load_graph()
    candidates, jd_names = asyncio.run(_load_pg())
    if not args.stage or args.stage == "A":
        total += stage_a(args.apply, positions, candidates, jd_names)
    if not args.stage or args.stage == "B":
        total += stage_b(positions, candidates, jd_names)
    if args.reaggregate:
        from scripts.cleanup_graph import _reaggregate
        result = _reaggregate()
        logger.info("重聚合完成: %s", result)

    logger.info("合计: %s 项%s", total, "（--apply 执行）" if args.apply else "（待处理）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
