"""技能归一化同步：SBERT 聚类回写 Skill.normalized_name + SIMILAR_TO 关系。

设计文档 §5.3：Sentence-BERT 编码 → 层次聚类（阈值 0.25）→ 词典兜底，
输出 standard + confidence；回写 `Skill.normalized_name`，同簇相似度
≥ 0.85 自动建 `SIMILAR_TO {similarity}` 关系。

幂等：normalized_name 按技能名 SET，SIMILAR_TO 用 MERGE，可安全重跑。

用法：
  python scripts/sync_skill_normalization.py            # 全量执行
  python scripts/sync_skill_normalization.py --dry-run  # 只统计不写图谱
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.logging import setup_logging

logger = setup_logging("sync_skill_normalization")

from app.core.database import neo4j_driver
from app.services.extraction.normalization import (
    SkillNormalizer,
    guard_cluster_distribution,
)


def load_skill_names() -> list[str]:
    """读取图谱全部 Skill.name（无 Skill 节点时返回空列表）。"""
    with neo4j_driver.session() as session:
        rows = session.run("MATCH (s:Skill) RETURN s.name AS name").data()
    return [r["name"] for r in rows if r.get("name")]


def write_back(standard: str, name: str, confidence: float, dry_run: bool) -> None:
    """回写：SET normalized_name；置信度存边属性供审计。"""
    if dry_run:
        return
    with neo4j_driver.session() as session:
        session.run(
            "MATCH (s:Skill {name: $name}) SET s.normalized_name = $standard",
            name=name,
            standard=standard,
        )
        if name != standard:
            session.run(
                """
                MATCH (a:Skill {name: $standard}), (b:Skill {name: $name})
                MERGE (a)-[r:SIMILAR_TO]->(b)
                SET r.similarity = $similarity
                """,
                standard=standard,
                name=name,
                similarity=confidence,
            )


def main(dry_run: bool = False) -> None:
    names = load_skill_names()
    if not names:
        logger.warning("图谱无 Skill 节点，跳过")
        return

    normalizer = SkillNormalizer()
    normalized = normalizer.normalize_many(names)
    if not normalized:
        logger.warning("归一化无输出（可能是模型不可用且无词典命中），跳过")
        return

    # ── 门禁预检（P0/P1）：簇分布异常拒绝写库；dry-run 仅报告不中断 ──
    # 08-13 事故：单链接漂移把 1185 个无关技能并入"2D可视化"簇后直接入库。
    # dry-run 模式下同样输出门禁结论，供人工 Review 后再 --apply 写库。
    from collections import Counter

    cnt = Counter(r.standard for r in normalized.values())
    top5 = ", ".join(f"{k}({v})" for k, v in cnt.most_common(5))
    logger.info("簇分布摘要: 总数=%s 映射率=%.1f%% 最大簇=%s Top5=%s",
                len(normalized),
                100 * sum(1 for n, r in normalized.items() if r.standard != n) / len(normalized),
                cnt.most_common(1)[0],
                top5)

    try:
        guard = guard_cluster_distribution(normalized)
    except ValueError as e:
        logger.error("门禁拦截：%s", e)
        if not dry_run:
            logger.error("已拒绝写库（如需强制写库请人工确认后使用 --force）")
            return
    else:
        logger.info("门禁通过: %s", guard)

    changed = sum(1 for n, r in normalized.items() if r.standard != n)
    logger.info("技能总数: %s，归一化后变更: %s", len(names), changed)

    # 回写 normalized_name（含自指 SET，幂等）
    for name, res in normalized.items():
        write_back(res.standard, name, res.confidence, dry_run)

    # SIMILAR_TO 关系（同簇相似度 ≥ 0.85，非自指）
    pairs = normalizer.similar_pairs(normalized)
    logger.info("SIMILAR_TO 关系候选: %s", len(pairs))
    for standard, member, sim in pairs:
        write_back(standard, member, sim, dry_run)

    if dry_run:
        logger.info("dry-run 完成，未写图谱")
    else:
        logger.info("完成：normalized_name 与 SIMILAR_TO 已回写")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="技能归一化同步")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写图谱（含门禁预检）")
    parser.add_argument("--force", action="store_true", help="门禁拦截时仍强制写库（人工确认后使用）")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
