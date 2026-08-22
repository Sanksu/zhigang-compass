"""icourse163 课程 URL 回填（08-22 实证修复）。

背景：icourse163 爬虫曾按 /course/{courseId} 拼课程链接，缺学校简称前缀，
纯数字路径 404 → commonError.htm 错误页（存量 891 门全部不可访问）。
真实可访问格式为 /course/{schoolPanel.shortName}-{courseId}（08-22 实测
NUIST-1468629165 200 / 1468629165 404）。shortName 已存于 raw_text 的
mocCourseCardDto.schoolPanel 节点，无需重爬。

更新三处：PG course_raw.source_url 列、course_raw.snapshot["source_url"]、
Neo4j Course.source_url（按 source_id 对齐）。raw_text 无 shortName 的行
跳过并在输出中列出（可人工核查）。旧值可由 source_id 确定性重建，无需清单。

用法（cwd=backend）：
    python -m scripts.backfill_icourse163_urls                # dry-run，只打印统计
    python -m scripts.backfill_icourse163_urls --apply        # 执行更新
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from app.core.database import async_session_factory, neo4j_driver
from app.models.raw import CourseRaw

SOURCE = "icourse163"


def _short_name_from_raw_text(raw_text: str) -> str | None:
    """从 raw_text RPC 快照提取 schoolPanel.shortName；缺失返回 None。"""
    if not raw_text:
        return None
    try:
        data = json.loads(raw_text)
    except (TypeError, json.JSONDecodeError):
        return None
    school = (
        ((data.get("mocCourseCard") or {}).get("mocCourseCardDto") or {}).get("schoolPanel")
        or {}
    )
    short = school.get("shortName")
    return str(short).strip() if short else None


def _fixed_url(short_name: str, source_id: str) -> str:
    return f"https://www.icourse163.org/course/{short_name}-{source_id}"


async def _collect_plans() -> tuple[list[tuple[str, str, str]], list[str]]:
    """扫描 course_raw，返回 (source_id, 旧URL, 新URL) 计划与缺 shortName 的 source_id。"""
    plans: list[tuple[str, str, str]] = []
    missing: list[str] = []
    async with async_session_factory() as session:
        rows = (
            await session.scalars(select(CourseRaw).where(CourseRaw.source == SOURCE))
        ).all()
    for row in rows:
        short = _short_name_from_raw_text(row.raw_text)
        if not short:
            missing.append(row.source_id)
            continue
        new_url = _fixed_url(short, row.source_id)
        if row.source_url != new_url:
            plans.append((row.source_id, row.source_url, new_url))
    return plans, missing


async def _apply_pg(plans: list[tuple[str, str, str]]) -> int:
    updated = 0
    async with async_session_factory() as session:
        for source_id, _old, new_url in plans:
            row = (
                await session.scalars(
                    select(CourseRaw).where(
                        CourseRaw.source == SOURCE, CourseRaw.source_id == source_id
                    )
                )
            ).one()
            row.source_url = new_url
            snapshot = dict(row.snapshot or {})
            snapshot["source_url"] = new_url
            row.snapshot = snapshot
            updated += 1
        await session.commit()
    return updated


def _apply_neo4j(plans: list[tuple[str, str, str]]) -> int:
    data = [{"source_id": sid, "url": new} for sid, _old, new in plans]
    with neo4j_driver.session() as session:
        summary = session.run(
            f"""
            UNWIND $rows AS row
            MATCH (c:Course {{source: '{SOURCE}', source_id: row.source_id}})
            SET c.source_url = row.url
            RETURN count(c) AS n
            """,
            rows=data,
        ).single()
        return summary["n"] if summary else 0


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="执行更新（缺省 dry-run）")
    args = parser.parse_args()

    plans, missing = await _collect_plans()
    print(f"icourse163 待更新 {len(plans)} 条；缺 shortName 跳过 {len(missing)} 条")
    for sid in missing[:20]:
        print(f"  [缺 shortName] {sid}")
    if not args.apply:
        for sid, old, new in plans[:5]:
            print(f"  [示例] {sid}: {old} -> {new}")
        print("dry-run 结束（--apply 执行）")
        return

    pg_n = await _apply_pg(plans)
    neo_n = _apply_neo4j(plans)
    print(f"PG 更新 {pg_n} 条；Neo4j 更新 {neo_n} 条")


if __name__ == "__main__":
    asyncio.run(main())
