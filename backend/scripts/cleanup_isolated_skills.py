"""孤立技能分层清理（2026-08-13 图谱数据质量治理）。

背景：
- ETL 每轮全量导入后，部分技能节点不再被任何岗位 REQUIRES 引用（岗位聚合
  P2 过滤低频边、岗位消失、JD 重复清理等），形成"孤立技能"。
- 2026-08-13 实测：6593 技能中 5348 无岗位引用（81%），其中白名单 135 /
  课程引用 535 / SIMILAR_TO 端点 1040 / normalized_name 指向 1938 必须保留
  （与归一化、学习路径强耦合），安全删除子集 2043。

清理分层（与 2026-08-09 孤立技能治理口径一致，补充归一化上线后的新约束）：
1. 保留：白名单内（SKILL_WHITELIST，设计文档 §6.3 第三道防线）
2. 保留：有 LEARNABLE_VIA 课程引用（学习路径依赖）
3. 保留：SIMILAR_TO 端点或被他技能 normalized_name 指向（归一化簇结构，
   删除会破坏技能归一化索引——由算法岗在归一化重跑时统一收敛）
4. 删除：白名单外、无课程、无归一化引用的技能（DETACH DELETE，连带
   EVIDENCED_BY / BELONGS_TO / ALTERNATIVE_OF 边）
5. 删除后清理完全孤立 Evidence（Evidence 是 jd_raw 的图内冗余副本，
   删除前已抽查 source_url 在 jd_raw 100% 可命中，审计链不丢）

用法：
    uv run python scripts/cleanup_isolated_skills.py            # 执行清理
    uv run python scripts/cleanup_isolated_skills.py --dry-run  # 仅输出报告
"""

import argparse
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

import yaml

from app.core.logging import setup_logging

logger = setup_logging("cleanup_isolated_skills")

from app.core.database import neo4j_driver


def _load_whitelist() -> set[str]:
    with open(_BACKEND_DIR / "configs" / "skill_whitelist.yaml", encoding="utf-8") as f:
        return {item["name"] for item in yaml.safe_load(f)["skills"]}


def _classify() -> tuple[list[str], dict]:
    """返回 (安全删除子集, 分类统计)。"""
    wl = _load_whitelist()
    with neo4j_driver.session() as s:
        cand = s.run(
            """
            MATCH (sk:Skill)
            WHERE NOT EXISTS { (p:Position)-[:REQUIRES]->(sk) }
              AND NOT EXISTS { (sk)-[:LEARNABLE_VIA]->() }
            RETURN collect(sk.name) AS names
            """
        ).single()["names"]

        sim = set()
        for r in s.run(
            "MATCH (a:Skill)-[:SIMILAR_TO]->(b:Skill) RETURN a.name AS a, b.name AS b"
        ).data():
            sim.add(r["a"])
            sim.add(r["b"])

        nn = {
            r["nn"]
            for r in s.run(
                "MATCH (sk:Skill) WHERE sk.normalized_name IS NOT NULL "
                "RETURN sk.normalized_name AS nn"
            ).data()
        }

    in_wl = {n for n in cand if n in wl}
    no_wl = {n for n in cand if n not in wl}
    safe = sorted(no_wl - sim - nn)
    stats = {
        "isolated_total": len(cand),
        "in_whitelist": len(in_wl),
        "similar_to": len(no_wl & sim),
        "normalized_ref": len(no_wl & nn),
        "safe_to_delete": len(safe),
    }
    return safe, stats


def _delete(safe: list[str]) -> None:
    with neo4j_driver.session() as s:
        for i in range(0, len(safe), 400):
            s.run(
                "MATCH (sk:Skill) WHERE sk.name IN $names DETACH DELETE sk",
                names=safe[i : i + 400],
            )
        # 清理删除后产生的完全孤立 Evidence（无任何 EVIDENCED_BY 入边）
        iso = s.run(
            "MATCH (e:Evidence) WHERE NOT EXISTS { ()-[:EVIDENCED_BY]->(e) } "
            "RETURN count(e) AS n"
        ).single()["n"]
        if iso:
            s.run(
                "MATCH (e:Evidence) WHERE NOT EXISTS { ()-[:EVIDENCED_BY]->(e) } "
                "DETACH DELETE e"
            )
        logger.info("已清理完全孤立 Evidence %s 个", iso)


def main() -> None:
    parser = argparse.ArgumentParser(description="孤立技能分层清理")
    parser.add_argument("--dry-run", action="store_true", help="仅输出报告不执行删除")
    args = parser.parse_args()

    safe, stats = _classify()
    print("== 孤立技能分类统计 ==")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print(f"  保留理由：白名单(设计文档 §6.3 第三道防线) / 课程引用 / SIMILAR_TO / normalized_name")

    if not safe:
        print("无可清理项")
        return
    if args.dry_run:
        print(f"[dry-run] 待删除 {len(safe)} 个，未执行")
        return

    with neo4j_driver.session() as s:
        before = s.run("MATCH (sk:Skill) RETURN count(sk) AS n").single()["n"]
    _delete(safe)
    with neo4j_driver.session() as s:
        after = s.run("MATCH (sk:Skill) RETURN count(sk) AS n").single()["n"]
        iso = s.run(
            "MATCH (sk:Skill) WHERE NOT EXISTS { (p:Position)-[:REQUIRES]->(sk) } "
            "RETURN count(sk) AS n"
        ).single()["n"]
    print(f"Skill {before} -> {after}（删除 {before - after}），剩余孤立 {iso}")


if __name__ == "__main__":
    main()
