"""白名单冗余技能名图谱归并（数据治理 ③，2026-08-25 用户拍板）。

归并 4 对近义冗余标准名（configs/skill_whitelist.yaml 已同步删除，
SKILL_ALIAS 已补词面变体，本脚本负责 Neo4j 侧）：

    C语言 → C            深度学习算法 → 深度学习
    Vue 3 → Vue.js       ES6+         → JavaScript

保留判断（拍板记录）：C/C语言同指 ISO C；「深度学习算法」是「深度学习」
冗余写法；Vue 3/ES6+ 是版本号维度，白名单不带版本维度。Spring/Spring
Boot、SQL调优/SQL、ASP.NET Core/.NET 为真不同技能，不归并。

幂等：边迁移用 MERGE ON CREATE（重复执行安全），冗余节点不存在则跳过。
默认 dry-run（只打印计数），--apply 才执行。

用法：
    uv run python scripts/merge_redundant_skills.py           # dry-run
    uv run python scripts/merge_redundant_skills.py --apply   # 执行
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _driver():
    """创建 Neo4j 驱动（不走 app.core.database，避免其顶层 postgres engine
    副作用）。Neo4j-only 脚本在主机/容器跑都不因 postgres 缺失报无关错。
    从 app core settings 读 URI（环境变量带出 neo4j_uri/user/password）。
    """
    from neo4j import GraphDatabase

    from app.core.config import settings

    return GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )

MERGES: list[tuple[str, str]] = [
    ("C语言", "C"),
    ("深度学习算法", "深度学习"),
    ("Vue 3", "Vue.js"),
    ("ES6+", "JavaScript"),
]

# 技能节点涉及的边类型（图谱 schema：岗位→技能 REQUIRES；技能→技能
# PREREQUISITE_OF/BELONGS_TO/ALTERNATIVE_OF；技能→课程 LEARNABLE_VIA）
_IN_EDGES = ["REQUIRES"]
_OUT_EDGES = ["PREREQUISITE_OF", "BELONGS_TO", "ALTERNATIVE_OF", "LEARNABLE_VIA"]


def _stats(session, dup: str) -> dict:
    row = session.run(
        """
        OPTIONAL MATCH (d:Skill {name: $dup})
        OPTIONAL MATCH ()-[rin:REQUIRES]->(d)
        OPTIONAL MATCH (d)-[rout]->()
        RETURN d IS NOT NULL AS exists,
               count(DISTINCT rin) AS in_edges,
               count(DISTINCT rout) AS out_edges
        """,
        dup=dup,
    ).single()
    return row.data() if row else {"exists": False, "in_edges": 0, "out_edges": 0}


def _merge_one(tx, dup: str, keep: str) -> None:
    for kind in _IN_EDGES:
        tx.run(
            f"""
            MATCH (d:Skill {{name: $dup}})<-[r:{kind}]-(s)
            MATCH (k:Skill {{name: $keep}})
            MERGE (s)-[r2:{kind}]->(k)
            ON CREATE SET r2 = properties(r)
            DELETE r
            """,
            dup=dup, keep=keep,
        )
    for kind in _OUT_EDGES:
        tx.run(
            f"""
            MATCH (d:Skill {{name: $dup}})-[r:{kind}]->(t)
            MATCH (k:Skill {{name: $keep}})
            MERGE (k)-[r2:{kind}]->(t)
            ON CREATE SET r2 = properties(r)
            DELETE r
            """,
            dup=dup, keep=keep,
        )
    # freq 累加后删除冗余节点
    tx.run(
        """
        MATCH (d:Skill {name: $dup}), (k:Skill {name: $keep})
        SET k.freq = coalesce(k.freq, 0) + coalesce(d.freq, 0)
        DETACH DELETE d
        """,
        dup=dup, keep=keep,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="执行合并（默认 dry-run）")
    args = parser.parse_args()

    driver = _driver()
    with driver.session() as session:
        for dup, keep in MERGES:
            stats = _stats(session, dup)
            keep_exists = session.run(
                "MATCH (k:Skill {name: $keep}) RETURN count(k) AS c", keep=keep,
            ).single()["c"]
            flag = "" if (args.apply and keep_exists) else "  [dry-run]" if args.apply \
                else "  [将执行]" if keep_exists else "  ⚠️ 保留名节点缺失"
            print(
                f"{dup!r} → {keep!r}: 冗余节点存在={stats['exists']} "
                f"入边={stats['in_edges']} 出边={stats['out_edges']} "
                f"保留名存在={bool(keep_exists)}{flag}"
            )
            if args.apply and stats["exists"] and keep_exists:
                with session.begin_transaction() as tx:
                    _merge_one(tx, dup, keep)
                    tx.commit()
                print(f"    已合并 {dup!r} → {keep!r}")
    driver.close()
    print("\n完成" + ("" if args.apply else "（dry-run，--apply 执行）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
