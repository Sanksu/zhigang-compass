"""熟练度（level）轻量补抽脚本。

背景：JD 抽取 prompt 此前未指令提取熟练度，存量 717 条已抽取记录中
requirements.level 填充率仅 1.0%。本脚本对缺失 level 的技能做轻量补抽：
- 仅对该 JD 中 level 为空的技能发起一次小 LLM 调用（不动其他字段）
- 输出经 `normalize_proficiency` 归一化到三档（了解/熟悉→初级、掌握→中级、精通→高级）
- JD 无明确熟练度表述的技能不写（保持空，不武断判定）
- 幂等：已有 level 的技能跳过，重复执行安全

用法：
    uv run python scripts/backfill_proficiency.py             # 补抽全部存量
    uv run python scripts/backfill_proficiency.py --limit 50  # 每批 50 条（分批跑）
    uv run python scripts/backfill_proficiency.py --dry-run   # 仅统计缺失量，不调用 LLM

补抽完成后需重跑岗位聚合写回图谱：
    uv run python -c "import asyncio; from app.workers.tasks import aggregate_positions; print(asyncio.run(aggregate_positions({})))"
"""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))


class ProficiencyResult(BaseModel):
    """技能名 → 熟练度映射（LLM 补抽输出，null 表示 JD 无明确表述）。"""

    proficiencies: dict[str, Optional[str]] = Field(
        description="技能名 → 熟练度（初级/中级/高级）或 null"
    )


# 补抽专用 prompt（仅提取熟练度，与主抽取 prompt 解耦）
PROFICIENCY_SYSTEM_PROMPT = """你是招聘信息分析助手。从 JD 文本中判断指定技能的掌握程度。
仅输出文本中明确表述的熟练度，未提及的输出 null，不要自行推断。"""

PROFICIENCY_TASK = """判断以下 JD 中这些技能的掌握程度，输出 JSON（key=技能名，value=熟练度）。

熟练度三档映射：了解/熟悉→初级，掌握/熟练→中级，精通/深入→高级。

JD 文本：
{jd_text}

技能列表：{skills}

输出 JSON："""


def _extraction_of(row) -> dict:
    """取 JD 的 LLM 抽取结果。"""
    return (row.snapshot or {}).get("extraction") or {}


async def count_missing() -> int:
    """统计缺 level 技能的 JD 数与技能数（dry-run）。"""
    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.raw import JDRaw

    async with async_session_factory() as session:
        rows = (await session.scalars(
            select(JDRaw).where(JDRaw.snapshot["extraction"].astext.isnot(None))
        )).all()
    jd_missing = 0
    skill_missing = 0
    for r in rows:
        reqs = _extraction_of(r).get("requirements") or []
        missing = [q for q in reqs if not q.get("level")]
        if missing:
            jd_missing += 1
            skill_missing += len(missing)
    return jd_missing, skill_missing


async def backfill_proficiency(limit: int = 0) -> dict:
    """补抽缺 level 的 JD（单条 LLM 调用，幂等）。"""
    import asyncio as _asyncio

    from sqlalchemy import select

    from app.core.database import async_session_factory
    from app.models.raw import JDRaw
    from app.services.extraction.dictionary import normalize_proficiency
    from app.services.extraction.llm_provider import LLMProviderChain
    from app.workers.tasks import _BATCH_REQUEST_INTERVAL, _build_jd_text

    llm = LLMProviderChain()
    stats = {"processed": 0, "filled": 0, "skipped": 0, "failed": []}

    async with async_session_factory() as session:
        stmt = (
            select(JDRaw)
            .where(JDRaw.snapshot["extraction"].astext.isnot(None))
            .order_by(JDRaw.id.asc())
        )
        if limit > 0:
            stmt = stmt.limit(limit)
        rows = (await session.scalars(stmt)).all()

        for row in rows:
            reqs = _extraction_of(row).get("requirements") or []
            missing = [q.get("skill_name", "") for q in reqs if not q.get("level") and q.get("skill_name")]
            if not missing:
                stats["skipped"] += 1
                continue

            jd_text = _build_jd_text(row.snapshot or {}, row.raw_text or "")
            prompt = PROFICIENCY_TASK.format(jd_text=jd_text, skills=", ".join(missing))
            try:
                result = llm.extract_structured(
                    prompt, ProficiencyResult, system_prompt=PROFICIENCY_SYSTEM_PROMPT
                )
            except Exception as e:  # LLM 不可用/失败：单条跳过，不阻塞整体
                stats["failed"].append({"jd_id": row.id, "error": str(e)[:200]})
                continue

            # 归一化写回（已有 level 的 req 不动；未命中/null 保持空）
            snap = dict(row.snapshot or {})
            ext = dict(snap.get("extraction") or {})
            new_reqs = []
            for q in ext.get("requirements") or []:
                q = dict(q)
                if not q.get("level") and q.get("skill_name") in result.proficiencies:
                    level = normalize_proficiency(result.proficiencies.get(q["skill_name"]) or "")
                    if level:
                        q["level"] = level
                        stats["filled"] += 1
                new_reqs.append(q)
            ext["requirements"] = new_reqs
            snap["extraction"] = ext
            row.snapshot = snap
            stats["processed"] += 1
            await _asyncio.sleep(_BATCH_REQUEST_INTERVAL)

        await session.commit()

    return stats


async def main(limit: int, dry_run: bool) -> None:
    jd_missing, skill_missing = await count_missing()
    print(f"缺 level：{jd_missing} 条 JD / {skill_missing} 个技能")
    if dry_run:
        print("dry-run：未调用 LLM，未写库")
        return
    stats = await backfill_proficiency(limit=limit)
    print(f"补抽完成：处理 {stats['processed']} 条 JD，新填 {stats['filled']} 个技能，"
          f"跳过 {stats['skipped']} 条（已完整），失败 {len(stats['failed'])} 条")
    for f in stats["failed"][:5]:
        print("  FAIL:", f)
    print("\n提示：补抽后重跑岗位聚合写回图谱 level（见脚本头部命令）。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="熟练度 level 轻量补抽")
    parser.add_argument("--limit", type=int, default=0, help="最多处理条数（0=全部）")
    parser.add_argument("--dry-run", action="store_true", help="仅统计缺失量，不调用 LLM")
    args = parser.parse_args()
    asyncio.run(main(args.limit, args.dry_run))
