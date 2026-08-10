"""图谱清理：删除幻觉技能 + 合并重复岗位（一次性历史数据清理）。

清理口径（与抽取管线防复发逻辑一致）：
1. 技能过滤：SKILL_STOPWORDS 黑名单技能直接删除（行业/业务领域词，如 保险/车联网）
2. 岗位合并：按 normalize_position_name 归一化，同标准名岗位重连关系后合并
3. 重新聚合岗位（P2 口径，复用 aggregation.py）并对齐图谱：清除聚合输出之外的
   REQUIRES 边（SimHash 重复 JD 独有技能、大岗位 hit<2 一次性噪声）、清理白名单外
   且无任何聚合输出边的孤立技能节点

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
    """删除黑名单停用词技能节点。

    白名单外低频技能（幻觉高发区）不再在此删除：其低频判定与聚合口径不一致
    （此处按图内边数<2、聚合按 jd_count≥10 且 hit<2），会造成"聚合要输出的技能
    节点被删、边无法落地"。低频边清理统一在 _reaggregate 中按聚合结果处理。
    """
    stopword_hits = _count(
        session,
        "MATCH (sk:Skill) WHERE sk.name IN $names RETURN count(sk) AS c",
        names=list(SKILL_STOPWORDS),
    )
    if dry_run:
        return {"stopword": stopword_hits}

    with session.begin_transaction() as tx:
        tx.run(
            "MATCH (sk:Skill) WHERE sk.name IN $names DETACH DELETE sk",
            names=list(SKILL_STOPWORDS),
        )
    return {"stopword": stopword_hits}


def merge_positions(session, dry_run: bool) -> dict:
    """按归一化岗位名合并重复 Position 节点。"""
    rows = session.run("MATCH (p:Position) RETURN p.id AS id, p.name AS name").data()
    groups: dict[str, list[dict]] = {}
    for r in rows:
        std = normalize_position_name(r["name"])
        groups.setdefault(std, []).append(r)

    merged = 0
    plans: list[dict] = []
    empty_nodes: list[str] = []
    for std, members in groups.items():
        # 空族：岗位名无法归一化（泛词/过滤词），无标准名可合并，直接删除而非
        # 合并成空名主节点（空名节点会污染聚合与快照）
        if not std:
            empty_nodes.extend(m["id"] for m in members)
            continue
        if len(members) <= 1:
            continue
        members.sort(key=lambda m: m["name"])  # 稳定顺序，首个为主节点
        primary, dups = members[0], members[1:]
        plans.append({"std": std, "primary": primary["id"], "dups": [d["id"] for d in dups]})
        merged += len(dups)

    if dry_run:
        return {"duplicate_groups": len(plans), "duplicate_nodes": merged,
                "empty_nodes": len(empty_nodes), "samples": plans[:10]}

    with session.begin_transaction() as tx:
        # 空族节点不保留：无归一化标准名，属于抽取出错/泛词
        for nid in empty_nodes:
            tx.run("MATCH (d:Position {id: $nid}) DETACH DELETE d", nid=nid)
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
    """重新聚合岗位（Position.freq + REQUIRES weight/source_count + 对齐删除）。

    复用 app/services/kg/aggregation.py 的 P2 口径（must 三重条件：hit≥3 样本保护
    + JD 覆盖率≥15% + must 标注占比>1/2；大岗位低频边过滤；跨域降权），与
    rebuild_graph 重放后一致。历史坑：本脚本曾自实现聚合（must/hit≥0.5 且无低频
    过滤），导致清理后低频边复活、口径与 aggregation.py 漂移。

    聚合输出对齐删除已内置于 write_aggregates（P1-1 衰退技能移除）：按本次聚合
    输出清除其外的 REQUIRES 边（SimHash 重复 JD 独有技能、大岗位 hit<2 一次性
    噪声、JD 已消失的衰退技能），且跳过 PositionEditLog 标记的人工编辑岗位，
    防止聚合打回人工调整。本脚本不再重复清理，仅补充清理白名单外、无聚合输出
    且无 REQUIRES 入边的孤立幻觉技能节点。
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from app.core.database import async_session_factory, neo4j_driver
    from app.models.raw import JDRaw
    from app.services.kg.aggregation import build_aggregates, build_edges, write_aggregates

    async def _load_rows():
        async with async_session_factory() as s:
            return (await s.scalars(
                select(JDRaw).where(JDRaw.snapshot["extraction"].astext.isnot(None))
            )).all()

    rows = asyncio.run(_load_rows())
    now = datetime.now(timezone(timedelta(hours=8))).isoformat()
    agg = build_aggregates(rows)
    edges = build_edges(agg)
    # write_aggregates 用 `with session:` 关闭了传入 session，孤立节点清理另开新 session。
    with neo4j_driver.session() as session:
        result = write_aggregates(session, agg, now)
    # 清理孤立幻觉技能节点：白名单外、无任何聚合输出边、无 REQUIRES 入边。
    # 白名单外但聚合输出的技能（高频真实技能）保留；白名单内节点即使无输出也
    # 保留（可能被手工/后续复用），不武断删除。
    all_output_skills = {e["skill"] for e in edges}
    with neo4j_driver.session() as session:
        removed_nodes = session.run(
            """
            MATCH (sk:Skill)
            WHERE NOT sk.name IN $whitelist
              AND NOT sk.name IN $output
              AND NOT exists((sk)<-[:REQUIRES]-())
            DETACH DELETE sk RETURN count(sk) AS c
            """,
            whitelist=list(SKILL_WHITELIST),
            output=list(all_output_skills),
        ).single()["c"]
    result["removed_orphan_skills"] = removed_nodes
    return result


def dedup_evidences(session, dry_run: bool) -> dict:
    """清理重复 Evidence 节点：同 (source, source_url) 仅保留最新一份。

    Evidence 由 kg_service.import_jd 每次 CREATE（每批导入留证，非幂等），
    同一 JD 原文重复导入会堆积多份 Evidence。按来源原文 URL 归组，
    保留 crawled_at 最新的一份（含 HAS_EVIDENCE/EVIDENCED_BY 关系），
    删除其余节点及关联边——审计口径保留"最新一次导入"证据，清理重复留档。
    """
    rows = session.run(
        """
        MATCH (e:Evidence)
        WHERE e.source_url IS NOT NULL AND e.source_url <> ''
        RETURN e.source_url AS url, e.id AS id, e.crawled_at AS crawled
        """
    ).data()
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["url"], []).append(r)

    dups: list[str] = []
    for members in groups.values():
        if len(members) <= 1:
            continue
        # crawled_at 缺失时按 id 排序兜底（同批导入 id 递增）
        members.sort(
            key=lambda m: (m["crawled"] or "", m["id"] or ""),
            reverse=True,
        )
        dups.extend(m["id"] for m in members[1:])

    if dry_run or not dups:
        return {"groups": len(groups), "duplicate_nodes": len(dups)}

    with session.begin_transaction() as tx:
        tx.run(
            "MATCH (e:Evidence) WHERE e.id IN $ids DETACH DELETE e",
            ids=dups,
        )
    return {"groups": len(groups), "duplicate_nodes": len(dups)}


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

        print("\n[1/5] 技能过滤")
        skills = filter_skills(session, args.dry_run)
        print(f"  {skills}")

        print("\n[2/5] 删除空岗位")
        if args.dry_run:
            empty_count = _count(
                session, "MATCH (p:Position) WHERE p.name IS NULL OR p.name = '' RETURN count(p) AS c"
            )
            print(f"  空岗位 {empty_count} 个")
        else:
            with session.begin_transaction() as tx:
                result = tx.run(
                    "MATCH (p:Position) WHERE p.name IS NULL OR p.name = '' "
                    "DETACH DELETE p RETURN count(p) AS c"
                )
                print(f"  删除空岗位 {result.single()['c']} 个")

        print("\n[3/5] 岗位合并")
        pos = merge_positions(session, args.dry_run)
        print(f"  groups={pos['duplicate_groups']} nodes={pos['duplicate_nodes']}")
        for s in (pos.get("samples") or [])[:5]:
            print(f"    {s['std']} ← {len(s['dups'])+1} 个节点: 主={s['primary']} 副={s['dups']}")

        if not args.dry_run:
            print("\n[4/5] 重新聚合岗位（归一化岗位名）")
            result = _reaggregate()
            print(f"  {result}")

        print("\n[5/5] 重复 Evidence 清理（同源 URL 保留最新一份）")
        ev = dedup_evidences(session, args.dry_run)
        print(f"  groups={ev['groups']} duplicate_nodes={ev['duplicate_nodes']}")

        after = {
            "Position": _count(session, "MATCH (p:Position) RETURN count(p) AS c"),
            "Skill": _count(session, "MATCH (sk:Skill) RETURN count(sk) AS c"),
            "REQUIRES": _count(session, "MATCH ()-[r:REQUIRES]->() RETURN count(r) AS c"),
        }
        print(f"\n清理后: {after}")


if __name__ == "__main__":
    main()
