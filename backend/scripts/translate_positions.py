"""图谱英文岗位翻译：对已入图的英文岗位名翻译并归并到中文岗位族。

翻译口径与抽取管线一致（normalize_position_name / _EN_POSITION_MAP）：
1. 遍历图谱 Position，归一化后与原名不同（英文岗位）的列为翻译目标
2. 同标准名节点合并：重连 REQUIRES/HAS_EVIDENCE 到主节点后删除重复节点；
   组内已有同名中文节点时以它为主，否则首个节点改名
3. 重新聚合岗位（Position.freq / REQUIRES.weight 按 jd_raw 全量重算，幂等）

无法归类（金融/专业独有）的英文岗位归一化返回原名，脚本跳过不处理。

用法：
    python scripts/translate_positions.py --dry-run   # 仅预览翻译与合并计划，不写图
    python scripts/translate_positions.py              # 执行翻译 + 合并 + 重新聚合
"""

import argparse
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.database import neo4j_driver
from app.services.extraction.dictionary import normalize_position_name


def build_plan(session) -> dict:
    """计算翻译计划：{translations: [...], groups: [{std, primary, dups}]}。

    translations：所有会被改名的英文岗位 (原名, 目标名, freq)。
    groups：合并组（含改名），primary 优先取组内已存在目标名的节点。
    """
    rows = session.run(
        "MATCH (p:Position) RETURN p.id AS id, p.name AS name, p.freq AS freq"
    ).data()
    groups: dict[str, list[dict]] = {}
    translations: list[dict] = []
    for r in rows:
        std = normalize_position_name(r["name"])
        if not std:
            continue
        if std != r["name"]:
            translations.append({"from": r["name"], "to": std, "freq": r.get("freq")})
        groups.setdefault(std, []).append({"id": r["id"], "name": r["name"]})

    merge_groups = []
    for std, members in groups.items():
        primary = next((m for m in members if m["name"] == std), members[0])
        dups = [m for m in members if m["id"] != primary["id"]]
        if dups or primary["name"] != std:
            merge_groups.append({
                "std": std,
                "primary": primary,
                "dups": dups,
                "rename": primary["name"] != std,
            })
    return {"translations": translations, "groups": merge_groups}


def apply_plan(session, plan: dict) -> dict:
    """执行合并：重连关系 → 删除重复节点 → 主节点改名。"""
    merged = 0
    renamed = 0
    with session.begin_transaction() as tx:
        for g in plan["groups"]:
            for dup in g["dups"]:
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
                    primary=g["primary"]["id"], dup=dup["id"],
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
                    primary=g["primary"]["id"], dup=dup["id"],
                )
                tx.run("MATCH (d:Position {id: $dup}) DETACH DELETE d", dup=dup["id"])
                merged += 1
            if g["rename"]:
                tx.run(
                    "MATCH (p:Position {id: $id}) SET p.name = $std",
                    id=g["primary"]["id"], std=g["std"],
                )
                renamed += 1
    return {"merged": merged, "renamed": renamed}


def reaggregate() -> dict:
    """按归一化岗位名重新聚合（与 ETL 阶段 8 同口径，幂等覆盖写回）。"""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.raw import JDRaw
    from app.services.kg.aggregation import build_aggregates, write_aggregates

    async def _load():
        async with async_session_factory() as s:
            return (await s.scalars(
                select(JDRaw).where(JDRaw.snapshot["extraction"].astext.isnot(None))
            )).all()

    import asyncio

    rows = asyncio.run(_load())
    now = datetime.now(timezone(timedelta(hours=8))).isoformat()
    agg = build_aggregates(rows)
    return write_aggregates(neo4j_driver.session(), agg, now)


def main() -> None:
    parser = argparse.ArgumentParser(description="图谱英文岗位翻译 + 归并")
    parser.add_argument("--dry-run", action="store_true", help="仅预览翻译/合并计划，不写图")
    args = parser.parse_args()

    with neo4j_driver.session() as session:
        before = {
            "Position": session.run("MATCH (p:Position) RETURN count(p) AS c").single()["c"],
            "REQUIRES": session.run("MATCH ()-[r:REQUIRES]->() RETURN count(r) AS c").single()["c"],
        }
        print(f"翻译前: {before}")

        plan = build_plan(session)
        print(f"\n待翻译英文岗位: {len(plan['translations'])} 个（归一化目标 ≠ 原名）")
        for t in sorted(plan["translations"], key=lambda x: -(x.get("freq") or 0)):
            print(f"  {t['from']!r} (freq={t.get('freq')}) -> {t['to']!r}")

        print(f"\n合并组: {len(plan['groups'])} 组")
        for g in plan["groups"]:
            dup_names = [d["name"] for d in g["dups"]]
            action = "改名" if g["rename"] else "保留"
            print(f"  {g['std']!r}: 主={g['primary']['name']!r}({action})"
                  + (f" 并入={dup_names}" if dup_names else ""))

        if args.dry_run:
            print("\n--dry-run 结束，未写入图谱。去掉该参数执行。")
            return

        result = apply_plan(session, plan)
        print(f"\n执行合并: {result}")

        agg = reaggregate()
        print(f"重新聚合: {agg}")

        after = {
            "Position": session.run("MATCH (p:Position) RETURN count(p) AS c").single()["c"],
            "REQUIRES": session.run("MATCH ()-[r:REQUIRES]->() RETURN count(r) AS c").single()["c"],
        }
        print(f"\n翻译后: {after}")


if __name__ == "__main__":
    main()
