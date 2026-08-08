"""技能名迁移：按新清洗规则重放 jd_raw snapshot（技能抽取质量修复 A/B/C）。

背景（与 backend/app/services/extraction 三处修复配套）：
- A. post_processor.SUFFIXES 移除"服务"：修复历史"微服务"→"微"碎片（保留"微服务"）
- B. dictionary.normalize_skill 白名单词大小写统一：GO→Go、matlab→MATLAB、Echarts→ECharts 等
- C. dictionary.SKILL_WHITELIST 补充高频真实技能（SQL/AWS/Azure/…）

特殊修复：历史"微"碎片若 JD 原文（title/description）含"微服务"则还原为"微服务"。

幂等：clean_skill_name(normalize_skill(x)) 对已规范输入返回原值，可安全重跑。
本脚本只改 PostgreSQL（jd_raw.snapshot），图谱需另行执行 rebuild_graph 重放。

用法：
  python scripts/migrate_skill_names.py            # 执行并打印变更统计
  python scripts/migrate_skill_names.py --dry-run  # 只统计不写库
"""

import asyncio
import sys
from collections import Counter
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import async_session_factory
from app.models.raw import JDRaw
from app.services.extraction.post_processor import canonical_skill_name

# 历史碎片还原：技能名 == "微" 且 JD 原文含"微服务" → "微服务"
_FRAGMENT_RESTORE = {
    "微": "微服务",
}


def _jd_text(snapshot: dict) -> str:
    return " ".join([
        str(snapshot.get("title") or ""),
        str(snapshot.get("description") or ""),
    ])


def _remap(name: str, jd_text: str) -> str:
    """新规则清洗 + 历史碎片还原，返回（清洗后名, 是否变化）。"""
    cleaned = canonical_skill_name(name)
    if cleaned in _FRAGMENT_RESTORE and _FRAGMENT_RESTORE[cleaned] in jd_text:
        return _FRAGMENT_RESTORE[cleaned]
    return cleaned


async def migrate(dry_run: bool) -> None:
    changed_jds = 0
    name_changes: Counter = Counter()
    async with async_session_factory() as s:
        rows = (await s.scalars(select(JDRaw))).all()
        for r in rows:
            ext = (r.snapshot or {}).get("extraction") or {}
            touched = False
            # requirements 与 skills 同规则清洗（与 post_process 口径一致）
            for req in ext.get("requirements") or []:
                old = req.get("skill_name") or ""
                if not old:
                    continue
                new = _remap(old, _jd_text(r.snapshot))
                if new != old:
                    req["skill_name"] = new
                    touched = True
                    name_changes[(old, new)] += 1
            for sk in ext.get("skills") or []:
                old = sk.get("name") or ""
                if not old:
                    continue
                new = _remap(old, _jd_text(r.snapshot))
                if new != old:
                    sk["name"] = new
                    touched = True
            if touched:
                changed_jds += 1
                if not dry_run:
                    flag_modified(r, "snapshot")
        if not dry_run:
            await s.commit()

    print(f"涉及变更的 JD 数: {changed_jds}")
    print(f"技能名变更种类: {len(name_changes)}")
    for (old, new), cnt in sorted(name_changes.items(), key=lambda x: -x[1]):
        print(f"  {old!r} -> {new!r} (x{cnt})")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    asyncio.run(migrate(dry_run))
