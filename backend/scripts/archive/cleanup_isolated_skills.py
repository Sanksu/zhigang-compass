"""孤立技能分层清理（2026-08-13 图谱数据质量治理）。

背景：
- ETL 每轮全量导入后，部分技能节点不再被任何岗位 REQUIRES 引用（岗位聚合
  P2 过滤低频边、岗位消失、JD 重复清理等），形成"孤立技能"。
- 2026-08-13 实测：6593 技能中 5348 无岗位引用（81%），其中白名单 135 /
  课程引用 535 / SIMILAR_TO 端点 1040 / normalized_name 指向 1938 必须保留
  （与归一化、学习路径强耦合，集合存在重叠），安全删除子集 2043。

清理分层（与 2026-08-09 孤立技能治理口径一致，补充归一化上线后的新约束）：
1. 保留：白名单内（SKILL_WHITELIST，设计文档 §6.3 第三道防线）
2. 保留：有 LEARNABLE_VIA 课程引用（学习路径依赖）
3. 保留：SIMILAR_TO 端点或被他技能 normalized_name 指向（归一化簇结构，
   删除会破坏技能归一化索引——由算法岗在归一化重跑时统一收敛）
4. 删除：白名单外、无课程、无归一化引用的技能（DETACH DELETE，连带
   EVIDENCED_BY / BELONGS_TO / ALTERNATIVE_OF 边）
5. 技能删除后独立清理"完全孤立 Evidence"——判定为无任何入边
   （EVIDENCED_BY 与 HAS_EVIDENCE 均无；HAS_EVIDENCE 是岗位→原始 JD 的
   审计链，误删会破坏设计文档 §1.4.7 证据引用覆盖率 100% 验收指标）。

⚠️ 删除前证据追溯校验（08-09 项目记忆）：Evidence 是 jd_raw 的图内冗余
副本，删除前须抽查其 source_url 在 jd_raw 仍有记录（操作者职责，脚本不
连 PG）。2026-08-13 首次执行抽查 15 条命中 11 条，复核确认其余为查询
重复项误报，实际 100% 命中。

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


def _count_skills(session) -> int:
    return session.run("MATCH (sk:Skill) RETURN count(sk) AS n").single()["n"]


def _count_isolated_evidence(session) -> int:
    """无任何入边的 Evidence（EVIDENCED_BY 与 HAS_EVIDENCE 均无）才可清理。"""
    return session.run(
        "MATCH (e:Evidence) WHERE NOT EXISTS { ()-->(e) } RETURN count(e) AS n"
    ).single()["n"]


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

        similar_endpoints = set()
        for r in s.run(
            "MATCH (a:Skill)-[:SIMILAR_TO]->(b:Skill) RETURN a.name AS a, b.name AS b"
        ).data():
            similar_endpoints.add(r["a"])
            similar_endpoints.add(r["b"])

        normalized_refs = {
            r["nn"]
            for r in s.run(
                "MATCH (sk:Skill) WHERE sk.normalized_name IS NOT NULL "
                "RETURN sk.normalized_name AS nn"
            ).data()
        }

    in_wl = {n for n in cand if n in wl}
    no_wl = {n for n in cand if n not in wl}
    safe = sorted(no_wl - similar_endpoints - normalized_refs)
    stats = {
        "isolated_total": len(cand),
        "in_whitelist": len(in_wl),
        "similar_to": len(no_wl & similar_endpoints),
        "normalized_ref": len(no_wl & normalized_refs),
        "safe_to_delete": len(safe),
    }
    return safe, stats


def _delete_skills(safe: list[str]) -> None:
    with neo4j_driver.session() as s:
        for i in range(0, len(safe), 400):
            s.run(
                "MATCH (sk:Skill) WHERE sk.name IN $names DETACH DELETE sk",
                names=safe[i : i + 400],
            )


def _delete_isolated_evidence() -> int:
    """删除无任何入边的 Evidence，返回删除数（独立步骤，即使无技能可删也执行）。"""
    with neo4j_driver.session() as s:
        iso = _count_isolated_evidence(s)
        if iso:
            s.run(
                "MATCH (e:Evidence) WHERE NOT EXISTS { ()-->(e) } DETACH DELETE e"
            )
            logger.info("已清理完全孤立 Evidence %s 个", iso)
    return iso


def main() -> None:
    parser = argparse.ArgumentParser(description="孤立技能分层清理")
    parser.add_argument("--dry-run", action="store_true", help="仅输出报告不执行删除")
    args = parser.parse_args()

    safe, stats = _classify()
    print("== 孤立技能分类统计 ==")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print("  保留理由：白名单(设计文档 §6.3 第三道防线) / 课程引用 / SIMILAR_TO / normalized_name")

    if not safe:
        print("无可清理技能")
    elif args.dry_run:
        print(f"[dry-run] 待删除 {len(safe)} 个，未执行")
        return
    else:
        before = _count_skills(neo4j_driver.session())
        _delete_skills(safe)
        after = _count_skills(neo4j_driver.session())
        with neo4j_driver.session() as s:
            iso = s.run(
                "MATCH (sk:Skill) WHERE NOT EXISTS { (p:Position)-[:REQUIRES]->(sk) } "
                "RETURN count(sk) AS n"
            ).single()["n"]
        print(f"Skill {before} -> {after}（删除 {before - after}），剩余孤立 {iso}")

    # Evidence 清理为独立步骤：技能删除后无条件执行（防中断后孤立 Evidence 残留）
    if not args.dry_run:
        removed = _delete_isolated_evidence()
        print(f"清理完全孤立 Evidence: {removed}")


if __name__ == "__main__":
    main()
