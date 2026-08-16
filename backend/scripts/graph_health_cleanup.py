"""图谱健康自动治理脚本（2026-08-16 治理流程固化）。

整合 08-16 手工治理的三类操作，dry-run 报告 / --apply 执行（均先备份）：

阶段 A — 课程脏边：LEARNABLE_VIA 语义相似度 < SEVERE(0.30) 的边。
            仅处理**同语言对**（中-中 / 英-英）——跨语言对（中-英）SBERT
            sim 天然低，跳过保留（运行时门控兜底），避免误删合理边
            （2026-08-16 batch_extract 新技能验证后修正）
阶段 B — 孤立伪技能：全部 LEARNABLE_VIA 边均 < SUSPICIOUS(0.45)（**仅计
            有 similarity 属性的边**——NULL 是新边未评估，不算脏，防误删
            Transact-SQL 类新入图真实技能）+ 无岗位 REQUIRES + 无
            SIMILAR_TO/EVIDENCED_BY/PREREQUISITE_OF/BELONGS_TO/
            ALTERNATIVE_OF/EVOLVED_FROM（出入双向）
阶段 C — 教学领域伪技能：名称含 教育/教学/备考/考试/考证/辅导/应试
            （教学法/教育主题词，非可雇佣技能）且无岗位 REQUIRES——
            精确模式不含"学习"，不伤"机器学习/深度学习"

备份：reports/graph_health_{stage}_{date}.jsonl（每阶段独立文件）。
用法：
    python -m scripts.graph_health_cleanup           # dry-run 报告
    python -m scripts.graph_health_cleanup --apply   # 执行清理
    python -m scripts.graph_health_cleanup --stage A # 只跑指定阶段
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.database import neo4j_driver
from app.core.logging import setup_logging

logger = setup_logging("graph_health_cleanup")

# 分档阈值（与 audit_learnable_via 一致）：< SEVERE 严重脏（清理）；
# SEVERE~SUSPICIOUS 可疑（保留，人工档）
SEVERE = 0.30
SUSPICIOUS = 0.45

# 教学领域模式（不含"学习"——机器学习/深度学习是真实技能）
TEACHING_PATTERN = ".*(教育|教学|备考|考试|考证|辅导|应试).*"

_REL_TYPES = "SIMILAR_TO|EVIDENCED_BY|PREREQUISITE_OF|BELONGS_TO|ALTERNATIVE_OF|EVOLVED_FROM"

_CJK = re.compile(r"[\u4e00-\u9fff]")


def _same_language(a: str, b: str) -> bool:
    """同语言对判定（中-中 / 英-英）；跨语言对跳过（SBERT 跨语言 sim 天然低）。"""
    return _CJK.search(a) is not None and _CJK.search(b) is not None or (
        _CJK.search(a) is None and _CJK.search(b) is None
    )


def _backup(rows: list[dict], name: str) -> Path:
    """备份（dry-run 也写，便于人工复核清单）。"""
    date = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = ROOT / "reports" / f"graph_health_{name}_{date}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    logger.info(f"  备份: {path}（{len(rows)} 条）")
    return path


def stage_a_dirty_edges(semantic, apply: bool) -> int:
    """阶段 A：删除同语言对 sim < SEVERE 的 LEARNABLE_VIA 脏边。"""
    logger.info("阶段 A — 课程脏边（同语言 sim < 0.30；跨语言对跳过）")
    with neo4j_driver.session() as session:
        rows = session.run(
            "MATCH (s:Skill)-[r:LEARNABLE_VIA]->(c:Course) "
            "RETURN s.name AS skill, c.name AS course, elementId(r) AS rel_id"
        ).data()
    dirty, cross = [], []
    for e in rows:
        sim = semantic.similarity(e["skill"], e["course"])
        if sim < SEVERE:
            if _same_language(e["skill"], e["course"]):
                dirty.append({**e, "sim": round(sim, 3)})
            else:
                cross.append({**e, "sim": round(sim, 3)})
    logger.info(f"  同语言脏边: {len(dirty)} 条（跨语言保留 {len(cross)} 条）")
    if not dirty:
        return 0
    _backup(dirty, "dirty_edges")
    if not apply:
        logger.info("  (dry-run，--apply 才删除)")
        return len(dirty)
    with neo4j_driver.session() as session:
        ids = [e["rel_id"] for e in dirty]
        n = 0
        for i in range(0, len(ids), 200):
            batch = ids[i : i + 200]
            n += session.run(
                "MATCH ()-[r:LEARNABLE_VIA]->() WHERE elementId(r) IN $ids DETACH DELETE r RETURN count(r) AS n",
                ids=batch,
            ).single()["n"]
    logger.info(f"  已删除 {n} 条脏边")
    return n


def stage_b_isolated(semantic, apply: bool) -> int:
    """阶段 B：删除完全孤立的伪技能节点。

    全脏边判定**仅计有 similarity 属性的边**（NULL = 新边未评估，不算脏——
    2026-08-16 修正：batch_extract 新技能课程边无属性，误判会删真实技能）。
    """
    logger.info("阶段 B — 孤立伪技能（有明确低 sim 的全脏边 + 无岗位/证据/归一化依赖）")
    cypher = f"""
        MATCH (s:Skill)-[r:LEARNABLE_VIA]->(:Course)
        WHERE r.similarity IS NOT NULL
        WITH s, collect(r) AS rels
        WHERE all(x IN rels WHERE x.similarity < {SUSPICIOUS})
        OPTIONAL MATCH (p:Position)-[req:REQUIRES]->(s)
        WITH s, count(req) AS req_count WHERE req_count = 0
        WITH s
        WHERE NOT (s)-[:{_REL_TYPES}]->()
          AND NOT ()-[:{_REL_TYPES}]->(s)
        RETURN s.name AS name, s.id AS sid
    """
    with neo4j_driver.session() as session:
        rows = session.run(cypher).data()
    logger.info(f"  孤立伪技能: {len(rows)} 个")
    if not rows:
        return 0
    _backup(rows, "isolated_skills")
    if not apply:
        logger.info("  (dry-run，--apply 才删除)")
        return len(rows)
    with neo4j_driver.session() as session:
        n = session.run(
            cypher.replace("RETURN s.name AS name, s.id AS sid", "DETACH DELETE s RETURN count(s) AS n")
        ).single()["n"]
    logger.info(f"  已删除 {n} 个孤立伪技能")
    return n


def stage_c_teaching(semantic, apply: bool) -> int:
    """阶段 C：删除教学领域伪技能节点（模式匹配 + 无岗位依赖）。"""
    logger.info("阶段 C — 教学领域伪技能（教学法/教育主题词，无岗位依赖）")
    cypher = f"""
        MATCH (s:Skill)
        WHERE s.name =~ '{TEACHING_PATTERN}'
        OPTIONAL MATCH (p:Position)-[req:REQUIRES]->(s)
        WITH s, count(req) AS reqs WHERE reqs = 0
        RETURN s.name AS name, s.id AS sid
    """
    with neo4j_driver.session() as session:
        rows = session.run(cypher).data()
    logger.info(f"  教学领域伪技能: {len(rows)} 个")
    if not rows:
        return 0
    _backup(rows, "teaching_skills")
    if not apply:
        logger.info("  (dry-run，--apply 才删除)")
        return len(rows)
    with neo4j_driver.session() as session:
        n = session.run(
            cypher.replace("RETURN s.name AS name, s.id AS sid", "DETACH DELETE s RETURN count(s) AS n")
        ).single()["n"]
    logger.info(f"  已删除 {n} 个教学领域伪技能")
    return n


def main() -> int:
    parser = argparse.ArgumentParser(description="图谱健康自动治理（脏边/伪技能）")
    parser.add_argument("--apply", action="store_true", help="执行清理（默认 dry-run 只报告+备份）")
    parser.add_argument("--stage", choices=["A", "B", "C"], help="只跑指定阶段（默认全部）")
    args = parser.parse_args()

    from app.services.matching.semantic import SkillEmbedder

    semantic = SkillEmbedder.get()

    logger.info("=" * 60)
    logger.info(f"图谱健康治理 {'[执行]' if args.apply else '[dry-run]'}")
    logger.info("=" * 60)

    total = 0
    if not args.stage or args.stage == "A":
        total += stage_a_dirty_edges(semantic, args.apply)
    if not args.stage or args.stage == "B":
        total += stage_b_isolated(semantic, args.apply)
    if not args.stage or args.stage == "C":
        total += stage_c_teaching(semantic, args.apply)

    logger.info(f"合计: {total} 项{'已清理' if args.apply else '待清理（--apply 执行）'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

