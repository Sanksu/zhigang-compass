"""技能白名单扩充候选挖掘（设计文档 §6.3 第三道防线"未对齐走审核"机制化）。

从 jd_raw 结构化抽取结果挖掘白名单外的候选技能（含出现岗位数频次），
输出候选清单供人工审核后追加到 configs/skill_whitelist.yaml。
依赖真实数据驱动扩充（现有白名单 170 → 500+），不靠手写硬编码。

过滤规则：
- 复用 normalize_skill（别名/大小写统一）与 SKILL_STOPWORDS（行业/福利词）
- 岗位类后缀词（工程师/开发/专员…）非技能，剔除
- 经验描述碎片（"熟悉SQL""Linux部署经验"）非技能名，剔除
- 2 字符以内 / 纯数字 / 含分隔符的复合标签视为噪音

用法：
  python scripts/expand_skill_whitelist.py                 # 打印 Top-200 候选
  python scripts/expand_skill_whitelist.py --top 500        # 打印前 500
  python scripts/expand_skill_whitelist.py --output 候选.txt  # 输出到文件
"""

import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.core.database import async_session_factory
from app.services.extraction.dictionary import (
    SKILL_STOPWORDS,
    SKILL_WHITELIST,
    is_noise_skill,
    normalize_skill,
)


async def _load_outside_skills() -> Counter:
    """聚合 jd_raw 白名单外技能（每岗去重）出现岗位数。"""
    counter: Counter = Counter()
    async with async_session_factory() as session:
        rows = (await session.execute(text("SELECT snapshot FROM jd_raw"))).all()
    for (snap,) in rows:
        skills = (snap.get("extraction") or {}).get("skills") or []
        seen: set[str] = set()
        for sk in skills:
            name = sk.get("name") if isinstance(sk, dict) else sk
            if not name:
                continue
            norm = normalize_skill(str(name))
            # normalize_skill 已把别名归一到标准名，白名单内即已覆盖
            if norm in SKILL_WHITELIST:
                continue
            key = norm.lower()
            if key in seen:
                continue
            seen.add(key)
            counter[norm] += 1
    return counter


async def main(top: int, output: Path | None) -> None:
    counter = await _load_outside_skills()
    # 噪音过滤后按出现岗位数降序
    candidates = [(name, cnt) for name, cnt in counter.items() if not is_noise_skill(name)]
    candidates.sort(key=lambda x: (-x[1], x[0].lower()))
    print(f"白名单外原始技能: {len(counter)} | 噪音过滤后候选: {len(candidates)}")

    lines = []
    for name, cnt in candidates[:top]:
        lines.append(f"{cnt:5d}  {name}")
        print(f"{cnt:5d}  {name}")

    if output:
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"候选已写入: {output}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="技能白名单扩充候选挖掘")
    parser.add_argument("--top", type=int, default=200, help="输出候选数量")
    parser.add_argument("--output", type=Path, default=None, help="候选清单输出文件")
    args = parser.parse_args()
    asyncio.run(main(top=args.top, output=args.output))
