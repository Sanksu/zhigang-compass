"""图谱清理：删除幻觉技能 + 合并重复岗位（一次性历史数据清理）。

清理口径（与抽取管线防复发逻辑一致）：
1. 技能过滤：
   - SKILL_STOPWORDS 黑名单技能直接删除（行业/业务领域词，如 保险/车联网）
   - 白名单（SKILL_WHITELIST）外且 REQUIRES 边数 < 2 的技能删除（LLM 幻觉高发区）
2. 岗位合并：按 normalize_position_name 归一化，同标准名岗位重连关系后合并
3. 重新聚合岗位（Position.freq / REQUIRES.weight 重算）

注意：删除不可逆，运行前建议确认。可重复执行（第二次无目标可删）。

用法：
    python scripts/cleanup_graph.py
    python scripts/cleanup_graph.py --dry-run   # 仅报告，不删除
"""

import argparse
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.database import neo4j_driver
from app.services.extraction.dictionary import (
    SKILL_STOPWORDS,
    SKILL_WHITELIST,
    normalize_position_name,
)

import asyncio


def _count(session, cypher: str, **params) -> int:
    return session.run(cypher, **params).single()["c"]


def filter_skills(session, dry_run: bool) -> dict:
    """删除黑名单技能 + 白名单外低频技能。"""
    stopword_hits = _count(
        session,
        "MATCH (sk:Skill) WHERE sk.name IN $names RETURN count(sk) AS c",
        names=list(SKILL_STOPWORDS),
    )
    lowfreq_hits = _count(
        session,
        """
        MATCH (sk:Skill)
        WHERE NOT sk.name IN $whitelist
          AND size([(sk)<-[:REQUIRES]-() | 1]) < 2
        RETURN count(sk) AS c
        """,
        whitelist=list(SKILL_WHITELIST),
    )
    if dry_run:
        return {"stopword": stopword_hits, "lowfreq": lowfreq_hits}

    with session.begin_transaction() as tx:
        tx.run(
            "MATCH (sk:Skill) WHERE sk.name IN $names DETACH DELETE sk",
            names=list(SKILL_STOPWORDS),
        )
        tx.run(
            """
            MATCH (sk:Skill)
            WHERE NOT sk.name IN $whitelist
              AND size([(sk)<-[:REQUIRES]-() | 1]) < 2
            DETACH DELETE sk
            """,
            whitelist=list(SKILL_WHITELIST),
        )
    return {"stopword": stopword_hits, "lowfreq": lowfreq_hits}


def merge_positions(session, dry_run: bool) -> dict:
    """按归一化岗位名合并重复 Position 节点。"""
    rows = session.run("MATCH (p:Position) RETURN p.id AS id, p.name AS name").data()
    groups: dict[str, list[dict]] = {}
    for r in rows:
        std = normalize_position_name(r["name"])
        groups.setdefault(std, []).append(r)

    merged = 0
    plans: list[dict] = []
    for std, members in groups.items():
        if len(members) <= 1:
            continue
        members.sort(key=lambda m: m["name"])  # 稳定顺序，首个为主节点
        primary, dups = members[0], members[1:]
        plans.append({"std": std, "primary": primary["id"], "dups": [d["id"] for d in dups]})
        merged += len(dups)

    if dry_run:
        return {"duplicate_groups": len(plans), "duplicate_nodes": merged,
                "samples": plans[:10]}

    with session.begin_transaction() as tx:
        for p in plans:
            for dup in p["dups"]:
                # 重连 REQUIRES（Position→Skill/Tool）
                tx.run(
                    """
                    MATCH (d:Position {id: $dup})-[r:REQUIRES]->(t)
                    WITH d, t, r
                    MATCH (p:Position {id: $primary})
                    MERGE (p)-[r2:REQUIRES]->(t)
                    ON CREATE SET r2 = properties(r)
                    WITH d, r
                    DELETE r
                    """,
                    primary=p["primary"], dup=dup,
                )
                # 重连 HAS_EVIDENCE（Position→Evidence）
                tx.run(
                    """
                    MATCH (d:Position {id: $dup})-[r:HAS_EVIDENCE]->(e)
                    WITH d, e, r
                    MATCH (p:Position {id: $primary})
                    MERGE (p)-[:HAS_EVIDENCE]->(e)
                    WITH d, r
                    DELETE r
                    """,
                    primary=p["primary"], dup=dup,
                )
                tx.run("MATCH (d:Position {id: $dup}) DETACH DELETE d", dup=dup)
            tx.run(
                "MATCH (p:Position {id: $primary}) SET p.name = $std",
                primary=p["primary"], std=p["std"],
            )
    return {"duplicate_groups": len(plans), "duplicate_nodes": merged,
            "samples": plans[:10]}


def _reaggregate() -> dict:
    """按归一化岗位名重新聚合（Position.freq + REQUIRES weight/source_count）。

    自包含实现：聚合口径与 app/services/kg/aggregation.py 一致（weight must=0.8/nice=0.4），
    仅岗位名使用 normalize_position_name 以匹配清理后的图谱。
    """
    from collections import defaultdict
    from datetime import datetime, timedelta, timezone
    from statistics import median

    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.raw import JDRaw

    async def _load_rows():
        async with async_session_factory() as s:
            return (await s.scalars(
                select(JDRaw).where(JDRaw.snapshot["extraction"].astext.isnot(None))
            )).all()

    import re

    rows = asyncio.run(_load_rows())
    stats: dict[str, dict] = defaultdict(lambda: {
        "jd": 0, "years": [],
        "skills": defaultdict(lambda: {"hit": 0, "must": 0, "src": set()}),
    })
    for r in rows:
        snap = r.snapshot or {}
        ext = snap.get("extraction") or {}
        pos = normalize_position_name(ext.get("position_name") or "")
        if not pos:
            continue
        st = stats[pos]
        st["jd"] += 1
        m = re.search(r"(\d+)", str(snap.get("experience") or ""))
        if m:
            st["years"].append(float(m.group(1)))
        reqs = ext.get("requirements") or []
        for q in reqs:
            name = (q.get("skill_name") or "").strip()
            if not name:
                continue
            sk = st["skills"][name]
            sk["hit"] += 1
            sk["src"].add(r.source or "")
            if q.get("necessity") == "must":
                sk["must"] += 1

    now = datetime.now(timezone(timedelta(hours=8))).isoformat()
    positions, edges = [], []
    for pos, st in stats.items():
        positions.append({
            "pos": pos,
            "freq": st["jd"],
            "req_years": median(st["years"]) if st["years"] else None,
            "now": now,
        })
        for name, sk in st["skills"].items():
            is_must = sk["must"] / sk["hit"] >= 0.5
            edges.append({
                "pos": pos, "skill": name,
                "weight": 0.8 if is_must else 0.4,
                "necessity": "must" if is_must else "nice",
                "source_count": len(sk["src"]),
            })

    with neo4j_driver.session() as session:
        if positions:
            session.run(
                """
                UNWIND $items AS it
                MATCH (p:Position {name: it.pos})
                SET p.freq = it.freq, p.last_updated = it.now,
                    p.required_years = coalesce(it.req_years, p.required_years)
                """,
                items=positions,
            )
        if edges:
            session.run(
                """
                UNWIND $edges AS e
                MATCH (p:Position {name: e.pos}), (s:Skill {name: e.skill})
                MERGE (p)-[r:REQUIRES]->(s)
                SET r.weight = e.weight, r.necessity = e.necessity,
                    r.source_count = e.source_count
                """,
                edges=edges,
            )
    return {"positions": len(positions), "edges": len(edges)}


def main() -> None:
    parser = argparse.ArgumentParser(description="图谱清理（幻觉技能 + 重复岗位）")
    parser.add_argument("--dry-run", action="store_true", help="仅报告删除/合并量，不执行")
    args = parser.parse_args()

    with neo4j_driver.session() as session:
        before = {
            "Position": _count(session, "MATCH (p:Position) RETURN count(p) AS c"),
            "Skill": _count(session, "MATCH (sk:Skill) RETURN count(sk) AS c"),
            "REQUIRES": _count(session, "MATCH ()-[r:REQUIRES]->() RETURN count(r) AS c"),
        }
        print(f"清理前: {before}")

        print("\n[1/3] 技能过滤")
        skills = filter_skills(session, args.dry_run)
        print(f"  {skills}")

        print("\n[2/3] 岗位合并")
        pos = merge_positions(session, args.dry_run)
        print(f"  groups={pos['duplicate_groups']} nodes={pos['duplicate_nodes']}")
        for s in (pos.get("samples") or [])[:5]:
            print(f"    {s['std']} ← {len(s['dups'])+1} 个节点: 主={s['primary']} 副={s['dups']}")

        if not args.dry_run:
            print("\n[3/3] 重新聚合岗位（归一化岗位名）")
            result = _reaggregate()
            print(f"  {result}")

        after = {
            "Position": _count(session, "MATCH (p:Position) RETURN count(p) AS c"),
            "Skill": _count(session, "MATCH (sk:Skill) RETURN count(sk) AS c"),
            "REQUIRES": _count(session, "MATCH ()-[r:REQUIRES]->() RETURN count(r) AS c"),
        }
        print(f"\n清理后: {after}")


if __name__ == "__main__":
    main()
