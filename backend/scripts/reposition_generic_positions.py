"""失真兜底岗位技能路由回归校验：兜底族不应再作为归一化结果出现。

背景（2026-08-09 图谱质量治理）：
- `_POSITION_KEYWORDS` 中的通用兜底族（软件开发工程师/科学家/架构师/研究员/顾问/
  硬件工程师/解决方案工程师/专家）按标题词聚合，把方向各异的 JD 全部吸进同一节点，
  技能集合混聚多个方向，语义失真。
- 治理方案：移除这些兜底词的聚合能力，`normalize_position_name(name, skills=...)`
  命中兜底标题时改按 JD 技能内容路由到已有细分族；无技能或未命中路由返回空串（不入图）。
- 本脚本是回归校验工具：对全部已抽取 JD 重新归一化，若再出现兜底族名即路由缺口。

只读脚本：不修改任何数据库与代码，仅输出报告。

用法：
    uv run python scripts/reposition_generic_positions.py [--sample M]
"""

import argparse
import asyncio
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("reposition_generic_positions")

from sqlalchemy import select

from app.core.database import async_session_factory
from app.models.raw import JDRaw
from app.services.extraction.dictionary import (
    _route_position_by_skills,
    normalize_position_name,
)

# 治理目标：这些族名不再作为"标题兜底聚合"结果出现。
# 例外：算法工程师仍可作为纯通用算法技能（机器学习/深度学习/pytorch 等）的
# 合法归位目标（_route_position_by_skills 命中通用算法族返回本族），不算缺口。
AFFECTED = frozenset({
    "软件开发工程师", "科学家", "架构师", "研究员", "顾问",
    "硬件工程师", "解决方案工程师", "专家", "算法工程师",
})


async def collect_jds() -> list[dict]:
    """读取全部已抽取 JD：{position_name(原抽取), skills, jd_id, source}。"""
    rows = []
    async with async_session_factory() as session:
        result = await session.scalars(
            select(JDRaw).where(JDRaw.snapshot["extraction"].astext.isnot(None))
        )
        for r in result:
            ext = (r.snapshot or {}).get("extraction") or {}
            if not ext:
                continue
            rows.append({
                "jd_id": r.id,
                "source": r.source,
                "raw_position": ext.get("position_name") or "",
                "skills": [
                    s.get("name") for s in (ext.get("skills") or [])
                    if isinstance(s, dict) and s.get("name")
                ],
            })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="失真兜底岗位技能路由回归校验")
    parser.add_argument("--sample", type=int, default=8, help="缺口 JD 抽样数")
    args = parser.parse_args()

    jds = asyncio.run(collect_jds())
    logger.info("已抽取 JD 总数: %s", len(jds))

    gaps = []
    for jd in jds:
        pos = normalize_position_name(jd["raw_position"], skills=jd["skills"])
        if pos not in AFFECTED:
            continue
        # 算法工程师特例：纯通用算法技能合法归位，不视为缺口
        if pos == "算法工程师" and jd["skills"]:
            if _route_position_by_skills(jd["skills"]) == "算法工程师":
                continue
        gaps.append(jd)

    if not gaps:
        logger.info("未发现受影响兜底岗位的 JD（兜底族已全部按技能路由或入空，符合治理预期）")
        return 0

    logger.info("\n" + "=" * 76)
    logger.info(f"发现 {len(gaps)} 条 JD 仍归一化为兜底族（路由缺口，需补充技能路由规则）")
    logger.info("=" * 76)
    for jd in gaps[: args.sample]:
        logger.info(f"    jd={jd['jd_id']} src={jd['source']} "
                    f"岗位='{jd['raw_position']}' 技能=[{', '.join(jd['skills'][:12])}]")

    return 1


if __name__ == "__main__":
    sys.exit(main())
