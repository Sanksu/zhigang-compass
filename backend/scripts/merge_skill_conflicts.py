"""技能子图修复：合并大小写/别名冲突节点 + 清理泛词碎片 + 补写 category（一次性）。

配套《技能分类体系评估与优化方案》P0-1 / P1-2 / P1-3：
- P1-3 合并冲突组：按 normalize_skill 归一化，同名异构（Vue3/vue3/VUE3、AI Agent/AI agent、
  UniApp/uniapp、SpringMVC/SpringMvc 等大小写与别名变体）合并为规范名节点，
  重连全部关系后删除冗余节点（Skill.name 唯一约束：先删冗余再改主节点名）
- P1-2 清理泛词碎片：SKILL_STOPWORDS 新增的 系统/操作/网络/前端 等存量节点删除
- P0-1 补写 category：存量 Skill 节点按白名单分类回填（白名单外标注 未分类）

幂等：normalize_skill 对规范名返回自身，二次运行无冲突组可合并；category 按名 SET 可重跑。
只改图谱 Skill 节点，不动岗位/Course/Evidence。

用法：
  python scripts/merge_skill_conflicts.py --dry-run  # 仅报告，不写图
  python scripts/merge_skill_conflicts.py            # 执行
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import neo4j_driver
from app.services.extraction.dictionary import (
    SKILL_STOPWORDS,
    normalize_skill,
    skill_category,
)


def _load_skills(session) -> list[dict]:
    rows = session.run(
        """
        MATCH (sk:Skill)
        OPTIONAL MATCH (sk)<-[r:REQUIRES]-(p:Position)
        RETURN sk.id AS id, sk.name AS name, count(r) AS req_deg
        """
    ).data()
    return [{"id": r["id"], "name": r["name"], "req_deg": r["req_deg"]} for r in rows]


def _delete_stopword_nodes(session, names: list[str]) -> int:
    result = session.run(
        "MATCH (sk:Skill) WHERE sk.name IN $names DETACH DELETE sk RETURN count(sk) AS c",
        names=names,
    ).single()["c"]
    return result


# 副节点重连的目标类型（按端点标签对唯一确定）。
# 注意：动态关系类型（MERGE (p)-[r:rt]->(t)）在本项目 Neo4j 版本把变量当字面类型名，
# 会产出类型为 "rt" 的垃圾边，必须按 (源, 目标) 标签对用显式字面类型。
_OUTGOING_RULES = [
    (("Skill", "Course"), "LEARNABLE_VIA"),
    (("Skill", "Evidence"), "MENTIONED_IN"),
    (("Skill", "Skill"), "SIMILAR_TO"),
]
_INCOMING_RULES = [
    (("Position", "Skill"), "REQUIRES"),
    (("Skill", "Skill"), "SIMILAR_TO"),
]


def _rel_type_for(labels: list[str], rules: list[tuple[tuple[str, str], str]]) -> str | None:
    """按端点标签对查关系类型；未命中返回 None（跳过该边，防止误连）。"""
    for (src, dst), rel in rules:
        if src in labels and dst in labels:
            return rel
    return None


def _reconnect_outgoing(session, dup: str, primary: str) -> None:
    """副节点出边重连到主节点（按端点标签对映射类型，ON CREATE 复制属性）。"""
    for rule in _OUTGOING_RULES:
        session.run(
            f"""
            MATCH (d:Skill {{id: $dup}})-[r:{rule[1]}]->(t)
            WITH d, t, r
            MATCH (p:Skill {{id: $primary}})
            WHERE t <> p
            MERGE (p)-[r2:{rule[1]}]->(t)
            ON CREATE SET r2 = properties(r)
            DELETE r
            """,
            dup=dup, primary=primary,
        )


def _reconnect_incoming(session, dup: str, primary: str) -> None:
    """副节点入边重连到主节点（Position REQUIRES / 其他入边）。"""
    for rule in _INCOMING_RULES:
        session.run(
            f"""
            MATCH (t)-[r:{rule[1]}]->(d:Skill {{id: $dup}})
            WITH d, t, r
            MATCH (p:Skill {{id: $primary}})
            WHERE t <> p
            MERGE (t)-[r2:{rule[1]}]->(p)
            ON CREATE SET r2 = properties(r)
            DELETE r
            """,
            dup=dup, primary=primary,
        )


def _merge_groups(session, groups: list[dict]) -> dict:
    merged_nodes = 0
    for g in groups:
        primary = g["primary"]["id"]
        for dup in g["dups"]:
            dup_id = dup["id"]
            _reconnect_outgoing(session, dup_id, primary)
            _reconnect_incoming(session, dup_id, primary)
            session.run("MATCH (d:Skill {id: $dup}) DETACH DELETE d", dup=dup_id)
            merged_nodes += 1
        # 主节点改名到规范名（唯一约束冲突已通过删除冗余节点规避）+ 补写 category
        session.run(
            "MATCH (p:Skill {id: $primary}) SET p.name = $canonical, p.category = $category",
            primary=primary, canonical=g["canonical"], category=skill_category(g["canonical"]),
        )
    return {"groups": len(groups), "merged_nodes": merged_nodes}


def _backfill_category(session, skills: list[dict]) -> int:
    """存量 Skill 节点按 skill_category 回填 category（含白名单外 → 未分类）。"""
    items = [{"name": s["name"], "category": skill_category(s["name"])} for s in skills]
    if not items:
        return 0
    session.run(
        """
        UNWIND $items AS it
        MATCH (sk:Skill {name: it.name})
        SET sk.category = it.category
        """,
        items=items,
    )
    return len(items)


def main() -> None:
    parser = argparse.ArgumentParser(description="技能子图修复（P0-1/P1-2/P1-3）")
    parser.add_argument("--dry-run", action="store_true", help="仅报告，不写图")
    args = parser.parse_args()

    with neo4j_driver.session() as session:
        skills = _load_skills(session)
        before = {"Skill": len(skills)}

        # ---- P1-2 泛词碎片清理 ----
        stopword_names = [s["name"] for s in skills if s["name"] in SKILL_STOPWORDS]
        print(f"\n[1/3] P1-2 泛词碎片清理（SKILL_STOPWORDS 命中存量节点 {len(stopword_names)} 个）")
        for n in sorted(set(stopword_names)):
            print(f"  - {n!r}")
        if not args.dry_run and stopword_names:
            _delete_stopword_nodes(session, stopword_names)
        skills = [s for s in skills if s["name"] not in SKILL_STOPWORDS]

        # ---- P1-3 冲突节点合并 ----
        groups_by_canonical: dict[str, list[dict]] = {}
        for s in skills:
            groups_by_canonical.setdefault(normalize_skill(s["name"]), []).append(s)

        plans = []
        for canonical, members in sorted(groups_by_canonical.items()):
            if len(members) <= 1:
                continue
            # 归属最高的规范名：REQUIRES 度最高者为主节点（平局取名字典序）
            members.sort(key=lambda m: (-m["req_deg"], m["name"]))
            primary, dups = members[0], members[1:]
            plans.append({
                "canonical": canonical, "primary": primary,
                "dups": dups,
                "renames": primary["name"] != canonical,
            })

        print(f"\n[2/3] P1-3 大小写/别名冲突合并（{len(plans)} 组，冗余节点 "
              f"{sum(len(p['dups']) for p in plans)} 个）")
        for p in plans[:30]:
            detail = f" ← 主节点改名" if p["renames"] else ""
            print(f"  {p['canonical']!r}（{len(p['dups'])+1} 个节点，REQUIRES {p['primary']['req_deg']}）"
                  f"{detail}")
            for m in [p["primary"]] + p["dups"]:
                print(f"      {m['name']!r} req_deg={m['req_deg']}")
        if len(plans) > 30:
            print(f"  ... 其余 {len(plans)-30} 组略")

        if not args.dry_run and plans:
            _merge_groups(session, plans)

        # ---- P0-1 category 补写 ----
        # 合并后主节点名已更新，重新加载当前节点（删除/改名后按当前名补写）
        current = _load_skills(session)
        print(f"\n[3/3] P0-1 category 补写（当前 Skill 节点 {len(current)} 个）")
        if not args.dry_run:
            filled = _backfill_category(session, current)
            print(f"  回填 {filled} 个节点")

        # ---- 验证 ----
        after_skills = _load_skills(session)
        conflict_after = {}
        for s in after_skills:
            conflict_after.setdefault(normalize_skill(s["name"]), []).append(s)
        conflicts_after = {k: v for k, v in conflict_after.items() if len(v) > 1}
        cat_rows = session.run(
            "MATCH (sk:Skill) RETURN count(sk) AS total, "
            "count(CASE WHEN sk.category IS NOT NULL AND sk.category <> '' THEN 1 END) AS with_cat"
        ).single()
        print(f"\n验证（{'dry-run 预估' if args.dry_run else '执行后'}）:")
        print(f"  Skill 节点: {before['Skill']} → {len(after_skills)}")
        print(f"  冲突组: {len(plans)} → {len(conflicts_after)}")
        print(f"  category 落地率: {cat_rows['with_cat']}/{cat_rows['total']}")


if __name__ == "__main__":
    main()
