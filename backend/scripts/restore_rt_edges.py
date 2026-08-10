"""恢复 merge_skill_conflicts.py 早期版本误建的 :rt 字面类型边（一次性）。

背景：旧版重连查询把动态关系类型变量当字面类型名，产出 700 条类型为 "rt" 的边
（属性已复制、原边已删除）。本脚本按端点标签对恢复为正确类型：

  Position -[:rt]-> Skill    → REQUIRES（保留 weight/necessity/level/source_count）
  Skill    -[:rt]-> Evidence → MENTIONED_IN
  Skill    -[:rt]-> Course   → LEARNABLE_VIA
  Skill    -[:rt]-> Skill    → SIMILAR_TO

幂等：重复运行无 rt 边可处理；MERGE 保证不会产生重复边。
只改边类型，不动节点与属性。

用法：
  python scripts/restore_rt_edges.py --dry-run  # 仅报告，不写图
  python scripts/restore_rt_edges.py            # 执行
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import neo4j_driver

# (源标签, 目标标签) → 正确关系类型
_RESTORE_RULES = [
    (("Position", "Skill"), "REQUIRES"),
    (("Skill", "Evidence"), "MENTIONED_IN"),
    (("Skill", "Course"), "LEARNABLE_VIA"),
    (("Skill", "Skill"), "SIMILAR_TO"),
]


def _scan_rt(session) -> list[dict]:
    rows = session.run(
        """
        MATCH (a)-[r:rt]->(b)
        RETURN labels(a)[0] AS src, labels(b)[0] AS dst, count(*) AS n
        ORDER BY n DESC
        """
    ).data()
    return [{"src": r["src"], "dst": r["dst"], "n": r["n"]} for r in rows]


def _restore(session, src: str, dst: str, rel: str) -> int:
    # MERGE 幂等：已存在的正确类型边不会重复创建；属性只在新建时复制
    result = session.run(
        f"""
        MATCH (a:{src})-[r:rt]->(b:{dst})
        WITH a, b, r
        MERGE (a)-[r2:{rel}]->(b)
        ON CREATE SET r2 = properties(r)
        DELETE r
        RETURN count(r) AS fixed
        """,
    ).single()["fixed"]
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="恢复误建的 :rt 边")
    parser.add_argument("--dry-run", action="store_true", help="仅报告，不写图")
    args = parser.parse_args()

    with neo4j_driver.session() as session:
        combos = _scan_rt(session)
        total = sum(c["n"] for c in combos)
        if total == 0:
            print("无 :rt 边，无需恢复。")
            return

        print(f"发现 :rt 边 {total} 条，按端点标签对映射：")
        unknown = []
        for c in combos:
            rel = next((r for (s, d), r in _RESTORE_RULES if s == c["src"] and d == c["dst"]), None)
            if rel is None:
                unknown.append(c)
                print(f"  {c['src']} -[:rt]-> {c['dst']}: {c['n']} 条 → 未配置恢复规则，跳过（保留）")
            else:
                print(f"  {c['src']} -[:rt]-> {c['dst']}: {c['n']} 条 → {rel}")

        if unknown:
            print("\n警告：存在未配置规则的组合，先人工确认，勿盲目执行。")
            sys.exit(1)

        if args.dry_run:
            print("\n[dry-run] 未写图。")
            return

        fixed = 0
        for c in combos:
            rel = next(r for (s, d), r in _RESTORE_RULES if s == c["src"] and d == c["dst"])
            fixed += _restore(session, c["src"], c["dst"], rel)
            print(f"  已恢复 {c['src']} → {c['dst']}: {fixed} 条")

        # ---- 验证 ----
        rt_left = _scan_rt(session)
        left = sum(c["n"] for c in rt_left)
        print(f"\n验证：")
        print(f"  :rt 残留: {left}（应 0）")
        for t in ["REQUIRES", "LEARNABLE_VIA", "MENTIONED_IN", "SIMILAR_TO"]:
            c = session.run(f"MATCH ()-[r:{t}]->() RETURN count(r) AS c").single()["c"]
            print(f"  {t}: {c}")


if __name__ == "__main__":
    main()
