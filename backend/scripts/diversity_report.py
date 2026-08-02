"""数据多样性报告脚本（DA-M4-02）。

聚合四类 raw 表（jd/course/paper/community）的多样性指标，输出
`reports/diversity_{date}.json`（幂等覆盖）+ 控制台摘要。

指标口径（见 app/services/data_quality/diversity.py）：
- 源覆盖：每类数据的平台分布
- 岗位多样性：唯一岗位数 / Top-N / 每岗位平均技能数 / 技能 CR10 集中度
- 课程多样性：平台分布 / 唯一技能标签数
- 去重率：fingerprint 唯一性

用法：
    uv run python scripts/diversity_report.py            # 全量并写 reports/
    uv run python scripts/diversity_report.py --top 10   # 岗位 Top-N
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import select

from app.core.database import async_session_factory
from app.models.raw import CommunityRaw, CourseRaw, JDRaw, PaperRaw
from app.services.data_quality.diversity import (
    course_diversity,
    dedup_stats,
    position_diversity,
    source_distribution,
)


def _jd_items(rows) -> list[dict]:
    """jd_raw → 岗位多样性输入：归一化岗位名 + 抽取技能名列表。"""
    items = []
    for r in rows:
        ext = (r.snapshot or {}).get("extraction") or {}
        name = (ext.get("position_name") or "").strip()
        if not name:
            continue
        skills = [s.get("name") for s in (ext.get("skills") or []) if s.get("name")]
        items.append({"position_name": name, "skills": skills})
    return items


def _course_items(rows) -> list[dict]:
    """course_raw → 课程多样性输入：platform + 技能标签。"""
    items = []
    for r in rows:
        snap = r.snapshot or {}
        skills = snap.get("skills") or []
        if isinstance(skills, str):
            skills = [s.strip() for s in skills.split(",") if s.strip()]
        items.append({"platform": snap.get("platform", r.source), "skills": skills})
    return items


async def collect(top_n: int) -> dict:
    async with async_session_factory() as s:
        jd_rows = (await s.scalars(select(JDRaw))).all()
        course_rows = (await s.scalars(select(CourseRaw))).all()
        paper_rows = (await s.scalars(select(PaperRaw))).all()
        community_rows = (await s.scalars(select(CommunityRaw))).all()

    jd_dicts = [
        {"source": r.source, "fingerprint": r.fingerprint} for r in jd_rows
    ]
    return {
        "generated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
        "jd": {
            "total": len(jd_rows),
            "sources": source_distribution(jd_dicts),
            "dedup": dedup_stats(jd_dicts),
            "positions": position_diversity(_jd_items(jd_rows), top_n=top_n),
        },
        "course": {
            **course_diversity(_course_items(course_rows)),
            "dedup": dedup_stats(
                [{"fingerprint": r.fingerprint} for r in course_rows]
            ),
        },
        "paper": {
            "total": len(paper_rows),
            "sources": source_distribution([{"source": r.source} for r in paper_rows]),
        },
        "community": {
            "total": len(community_rows),
            "sources": source_distribution([{"source": r.source} for r in community_rows]),
        },
    }


def print_report(r: dict) -> None:
    print("=" * 56)
    print("智岗罗盘 — 数据多样性报告（DA-M4-02）")
    print("=" * 56)
    jd = r["jd"]
    pos = jd["positions"]
    print(f"JD: {jd['total']} 条 | 去重率 {jd['dedup']['duplicate_rate']:.1%} | 源 {len(jd['sources'])} 个")
    print(f"    源分布: " + ", ".join(f"{s['source']}={s['count']}" for s in jd["sources"]))
    print(f"岗位: 唯一 {pos['unique_positions']} / 总 {pos['total_positions']} | "
          f"每岗位均技能 {pos['avg_skills_per_position']} | 技能 CR10 集中度 {pos['cr10']:.1%}")
    print(f"     Top-{len(pos['top_positions'])}: " + ", ".join(
        f"{p['name']}({p['count']})" for p in pos["top_positions"][:5]
    ))
    course = r["course"]
    print(f"课程: {course['total_courses']} 门 | 平台 {len(course['platforms'])} 个 | "
          f"唯一技能标签 {course['unique_skill_tags']} | 去重率 {course['dedup']['duplicate_rate']:.1%}")
    print(f"论文: {r['paper']['total']} 条 | 社区: {r['community']['total']} 条")


async def main(top_n: int, write: bool) -> None:
    report = await collect(top_n)
    print_report(report)
    if write:
        report_dir = _BACKEND_DIR / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        path = report_dir / f"diversity_{datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d')}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"报告已写入: {path.relative_to(_BACKEND_DIR)}")
    else:
        print("预览模式（--no-write）：未写库")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="数据多样性报告")
    parser.add_argument("--top", type=int, default=10, help="岗位 Top-N")
    parser.add_argument("--no-write", action="store_true", help="仅预览不写报告")
    args = parser.parse_args()
    asyncio.run(main(args.top, write=not args.no_write))
