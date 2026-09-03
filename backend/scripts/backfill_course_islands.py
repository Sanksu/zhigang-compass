"""存量孤岛课程技能回填（一次性，清孤立课 558 门）。

背景：progress 跟踪 §6.0.8/§7 记录"孤岛课 528/562 门 = 采而未建边活数据"。
成因（2026-09-03 审计）：
  1. 旧版 enrich_course_skills 对 LLM 判定无技能的课程直接写 skills_enriched=true
     永久标记（#702 之前），这批课程永不重抽；
  2. enrich_course_skills 只处理 `crawled_at >= 近 7 天` 的新课程，存量孤立课程
     即使未标记也进不了处理池（T-05 当时的"存量不动"取舍）；
  3. 数据源不对称：icourse163/edx 爬虫本身不产 skills，孤岛集中在 icourse163（≈83%）。

本脚本不受 7 天窗口限制，对 **全部** `snapshot.skills` 为空的课程（含已永久标记的
旧数据、含之前 never-enriched 的存量）做一次性 LLM 抽取：
  - LLM 抽到技能 → 写回 snapshot.skills + skills_enriched=true，并复用
    import_course 建 LEARNABLE_VIA 边；
  - LLM 判空 / 不可用 / 全部被门控过滤 → 不为让路，清除 skills_enriched /
    skills_retry_at / skills_enrich_empty_fails（复用 #702「重置」语义：不把空技能
    课程永久标记死，保留未来重试/重跑本脚本的机会）。

**分批提交（可恢复）**：按 _BATCH 分批，每批 LLM 抽取完成后**立即** commit PG 并
建 Neo4j 边，再处理下一批。进程若在任意点被杀，已完成批次已持久化（含边），
重跑时已 enriched 且技能非空的课程不再命中目标，仅续跑剩余空技能课程——不丢工。

安全设计：
- 默认 dry-run：只读扫描，打印按源分布 + 数量 + 示例，不写库、不调 LLM。
- --apply 才实际抽取并写库/建边。重复执行幂等（只命中技能为空的行）。
- --source / --limit 可限定范围做受控批次（如先跑若干条验证）。绝不删 skills，
  只允许"空 → 有"或"保留空 + 清永久标记"。

用法（cwd=backend）：
    python -m scripts.backfill_course_islands                            # dry-run
    python -m scripts.backfill_course_islands --apply                    # 全量回填
    python -m scripts.backfill_course_islands --apply --source=icourse163 --limit=20
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select

from app.models.raw import CourseRaw

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backfill_course_islands")

# 每批抽取后即 commit PG + 建边的行数（进程被杀最多丢当前这一批的处理结果；
# 批次越小越稳健，但 commit/建边频次增高。09-03 被中断教训：全程单 commit 易全丢）
_BATCH = 10
_TZ_CN = "+08:00"


def _empty_predicate():
    """技能为空（null / 非数组 / 空数组）。"""
    return (
        (CourseRaw.snapshot["skills"].astext.is_(None))
        | (func.jsonb_typeof(CourseRaw.snapshot["skills"]) != "array")
        | (func.jsonb_array_length(CourseRaw.snapshot["skills"]) == 0)
    )


async def _targets(source: str | None, limit: int | None) -> list[dict]:
    """只读定位目标：全部技能为空的课程（不受 7 天窗口限制，含已永久标记行）。"""
    from app.core.database import async_session_factory

    stmt = select(CourseRaw).where(_empty_predicate()).order_by(CourseRaw.id.asc())
    if source:
        stmt = stmt.where(CourseRaw.source == source)
    if limit:
        stmt = stmt.limit(limit)
    async with async_session_factory() as session:
        rows = (await session.scalars(stmt)).all()
    return [
        {
            "id": r.id,
            "source": r.source,
            "source_id": r.source_id,
            "title": (r.snapshot or {}).get("title", "") or "",
            "description": (r.snapshot or {}).get("description", "") or "",
            "enriched": (r.snapshot or {}).get("skills_enriched"),
            "snapshot": dict(r.snapshot or {}),
        }
        for r in rows
    ]


def _load_llm():
    """课程技能抽取 LLM（不可用返回 None，脚本静默跳过该批抽取并保留标记）。"""
    try:
        from app.services.extraction.jd_extractor import JDExtractor

        return JDExtractor().llm
    except Exception as exc:  # pragma: no cover - 依赖缺失路径
        logger.warning("LLM 初始化失败: %s", exc)
        return None


def _extract_one(llm, title: str, description: str) -> list[str]:
    """抽取单个课程技能（复用 enrich_course_skills 同口径）。"""
    from app.services.extraction.course_skills import extract_course_skills

    try:
        return extract_course_skills(llm, title, description)
    except Exception as exc:  # pragma: no cover - 抽取内部静默降级兜底
        logger.warning("课程技能抽取异常 title=%r: %s", title[:30], exc)
        return []


async def _sync_edges(updated: list[dict]) -> dict:
    """将已写回技能的课程入图（复用 import_course 建 LEARNABLE_VIA 边 + Skill 节点）。

    返回 (imported, failed)。与 load_courses 同样用线程池跑同步 Neo4j 写，
    避免阻塞 asyncio 事件循环。
    """
    from app.core.database import neo4j_driver
    from app.services.kg.kg_service import import_course

    imported = 0
    failed = 0

    def _import_all():
        nonlocal imported, failed
        with neo4j_driver.session() as session:
            for snap in updated:
                try:
                    import_course(session, snap)
                    imported += 1
                except Exception as exc:
                    failed += 1
                    logger.warning("入图失败 %s/%s: %s", snap.get("source"), snap.get("source_id"), exc)

    await asyncio.to_thread(_import_all)
    return {"imported": imported, "failed": failed}


async def dry_run(source: str | None, limit: int | None) -> None:
    targets = await _targets(source, limit)
    by_source: dict[str, int] = {}
    marked = 0
    for t in targets:
        by_source[t["source"]] = by_source.get(t["source"], 0) + 1
        if t["enriched"] is True:
            marked += 1
    print(
        f"[dry-run] 技能为空课程 {len(targets)} 门"
        f"（其中已 skills_enriched=true 永久标记 {marked} 门，会一并重置/重抽）"
    )
    for s, n in sorted(by_source.items(), key=lambda kv: -kv[1]):
        print(f"  {s:<12} {n}")
    for t in targets[:10]:
        print(
            f"  id={t['id']} source={t['source']}/{t['source_id']} "
            f"enriched={bool(t['enriched'])} title={t['title'][:40]!r}"
        )
    if len(targets) > 10:
        print(f"  … 其余 {len(targets) - 10} 门未列出")
    print("（dry-run 未调用 LLM / 未写库；加 --apply 实际执行）")


async def apply(source: str | None, limit: int | None) -> None:
    llm = _load_llm()
    targets = await _targets(source, limit)
    if not targets:
        print("无目标课程，跳过")
        return

    total = len(targets)
    enriched = 0
    no_llm = 0
    empty_keep = 0
    import_ok = 0
    import_fail = 0

    batches = [targets[i : i + _BATCH] for i in range(0, total, _BATCH)]
    for bi, batch in enumerate(batches, 1):
        todo: dict[int, dict] = {}  # 本批内待落库的课程
        for t in batch:
            snap = t["snapshot"]
            skills = _extract_one(llm, t["title"], t["description"])
            if skills:
                snap["skills"] = skills
                snap["skills_enriched"] = True
                snap.pop("skills_retry_at", None)
                snap.pop("skills_enrich_empty_fails", None)
                todo[t["id"]] = snap
                enriched += 1
            elif llm is None:
                no_llm += 1
                continue
            else:
                # LLM 判空 / 全被门控过滤：清永久标记，保留重试机会（#702 语义）
                snap.pop("skills_enriched", None)
                snap.pop("skills_retry_at", None)
                snap.pop("skills_enrich_empty_fails", None)
                todo[t["id"]] = snap
                empty_keep += 1

        if not todo:
            logger.info("batch %d/%d 无改动", bi, len(batches))
            continue

        # 立即 commit 本批（进程被杀至多丢当前批 → 可恢复）
        from app.core.database import async_session_factory

        async with async_session_factory() as session:
            objs = (
                await session.scalars(select(CourseRaw).where(CourseRaw.id.in_(list(todo))))
            ).all()
            for o in objs:
                o.snapshot = todo[o.id]
            await session.commit()

        # 本批内抽到技能的课程立即入图建边
        import_snaps = [todo[i] for i in todo if todo[i].get("skills")]
        if import_snaps:
            res = await _sync_edges(import_snaps)
            import_ok += res["imported"]
            import_fail += res["failed"]

        logger.info(
            "batch %d/%d 提交完成: 本批 %d 门 | 累计 enriched=%d empty=%d import ok=%d fail=%d",
            bi, len(batches), len(todo), enriched, empty_keep, import_ok, import_fail,
        )

    await _report_islands()

    print(
        f"[apply] 完成: 目标 {total} | 抽到技能并写库/建边 {enriched}"
        f" | LLM 不可用跳过 {no_llm} | LLM 判空保留(解永久标记) {empty_keep}"
        f" | 入图 imported={import_ok} failed={import_fail}"
    )


async def _report_islands() -> None:
    """打印当前 Neo4j 孤岛课程数（按源），供前后对比。"""
    try:
        from app.core.database import neo4j_driver

        with neo4j_driver.session() as session:
            rows = session.run(
                "MATCH (c:Course) WHERE NOT (c)-[:LEARNABLE_VIA]-() "
                "RETURN c.source AS src, count(c) AS n ORDER BY n DESC"
            ).data()
            total = 0
            print("回填后 Neo4j 孤岛（无 LEARNABLE_VIA 边）分布：")
            for r in rows:
                total += int(r["n"])
                print(f"  {(r['src'] or 'unknown'):<12} {r['n']}")
            print(f"  合计 {total} 门")
    except Exception as exc:
        logger.warning("孤岛统计查询失败（不影响回填结果）: %s", exc)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="实际抽取并写库/建边（默认 dry-run 只读）")
    parser.add_argument("--source", default=None, help="仅处理指定源（如 icourse163）")
    parser.add_argument("--limit", type=int, default=None, help="最多处理 N 门（受控验证）")
    args = parser.parse_args()
    asyncio.run(
        apply(args.source, args.limit) if args.apply else dry_run(args.source, args.limit)
    )


if __name__ == "__main__":
    main()