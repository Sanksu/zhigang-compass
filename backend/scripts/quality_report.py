"""数据质量报告（DA-M3-07）。

聚合 jd_raw 的抽取与质量检测结果，输出文本报告：
- 数据规模与抽取覆盖率
- 时滞检测标记分布（SAI / 僵尸 / 抄袭）
- 通胀检测标记分布
- 技能频次 Top-N

用法：
    python scripts/quality_report.py
    python scripts/quality_report.py --top 10
"""

import argparse
import asyncio
import sys
from pathlib import Path
from collections import Counter

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("quality_report")

from sqlalchemy import select

from app.core.database import async_session_factory
from app.models.raw import JDRaw


def _flat(items: list[dict], key: str, field: str) -> list:
    out = []
    for it in items:
        val = (it or {}).get(key) or {}
        if isinstance(val, dict) and val.get(field):
            out.append(val[field])
    return out


async def collect(top_n: int) -> dict:
    async with async_session_factory() as s:
        rows = (await s.scalars(select(JDRaw))).all()

    total = len(rows)
    extracted = [r for r in rows if (r.snapshot or {}).get("extraction")]
    validated = [r for r in rows if (r.snapshot or {}).get("validation")]
    inflated = [r for r in rows if (r.snapshot or {}).get("inflation")]

    skill_counter: Counter = Counter()
    empty_extract = 0
    for r in extracted:
        ext = (r.snapshot or {}).get("extraction") or {}
        skills = [x.get("name") for x in (ext.get("skills") or []) if x.get("name")]
        skill_counter.update(skills)
        if not skills:
            empty_extract += 1

    def _sai_label(v: dict) -> str:
        sai = (v.get("sai") or {}).get("label", "unknown")
        zombie = (v.get("zombie") or {}).get("is_zombie", False)
        plag = (v.get("plagiarism") or {}).get("is_plagiarism", False)
        if plag:
            return "plagiarism"
        if zombie:
            return "zombie"
        return sai  # fresh / content_stale / content_obsolete

    validation_labels = Counter(_sai_label((r.snapshot or {}).get("validation") or {}) for r in validated)
    inflation_labels = Counter((r.snapshot or {}).get("inflation", {}).get("label", "unknown") for r in inflated)

    return {
        "total": total,
        "extracted": len(extracted),
        "empty_extract": empty_extract,
        "validated": len(validated),
        "inflated": len(inflated),
        "validation_labels": dict(validation_labels),
        "inflation_labels": dict(inflation_labels),
        "skill_count": len(skill_counter),
        "top_skills": skill_counter.most_common(top_n),
    }


def print_report(r: dict, top_n: int) -> None:
    logger.info("=" * 52)
    logger.info("智岗罗盘 — 数据质量报告（DA-M3-07）")
    logger.info("=" * 52)
    logger.info(f"数据规模: jd_raw {r['total']} 条")
    logger.info(f"LLM 抽取: {r['extracted']} 条（覆盖率 {r['extracted'] / max(r['total'], 1):.1%}）"
                f"，其中空抽取 {r['empty_extract']} 条")
    logger.info(f"独立技能: {r['skill_count']} 个")
    logger.info(f"时滞检测: {r['validated']} 条 | 标记分布 {r['validation_labels']}")
    logger.info(f"通胀检测: {r['inflated']} 条 | 标记分布 {r['inflation_labels']}")
    logger.info(f"Top-{top_n} 技能:")
    for name, cnt in r["top_skills"]:
        logger.info(f"  {name:<24} {cnt}")


async def main(top_n: int) -> None:
    report = await collect(top_n)
    print_report(report, top_n)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="数据质量报告")
    parser.add_argument("--top", type=int, default=10, help="技能频次 Top-N")
    args = parser.parse_args()
    asyncio.run(main(args.top))
