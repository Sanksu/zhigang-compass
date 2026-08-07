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
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.core.database import async_session_factory
from app.services.extraction.dictionary import (
    SKILL_ALIAS,
    SKILL_STOPWORDS,
    SKILL_WHITELIST,
    normalize_skill,
)

# 岗位类后缀/前缀：技能抽取结果中出现的岗位名，非技能
_POSITION_NOISE = re.compile(
    r"(工程师|技术员|开发工程师|开发$|专员|经理|主管|负责人|设计师|"
    r"架构师|分析师|科学家|研究员|顾问|专家|实习生|助理|店长)$"
)
# 经验/职责描述碎片
_EXPERIENCE_NOISE = re.compile(r"(经验|开发|部署|使用|掌握|熟悉|了解|能力|经验$|方向)")
# 明确非技能的通用碎片词（白名单语义覆盖不了的太泛词）
_GENERIC_NOISE = {
    "前端", "后端", "测试", "运维", "算法", "数据库", "大数据", "云",
    "安全", "网络", "搜索", "配置", "操作", "脚本", "报表", "开源",
    "移动", "桌面", "嵌入式", "数据", "框架", "平台", "系统", "项目",
    "团队", "业务", "产品", "架构", "开发", "技术", "管理", "设计",
    "工具", "接口", "协议", "引擎", "服务", "组件", "方案", "功能",
    "经验", "工作", "能力", "要求", "方向", "语言",
}

# 含分隔符的复合标签（如 "MySQL/Redis/MongoDB"、"5天/周"）——技能名不应含分隔符
_COMPOUND_NOISE = re.compile(r"[\/()（）]|天/|月/|年/")

# 别名键（口语变体）本身是候选的标准名来源，非噪音
_ALIAS_STANDARDS = set(SKILL_ALIAS.values())


def _is_noise(name: str) -> bool:
    """启发式噪音判定：非技能标签 / 泛词 / 描述碎片。"""
    if name in _GENERIC_NOISE or name in SKILL_STOPWORDS:
        return True
    if name in _ALIAS_STANDARDS:
        return False  # 别名对应的标准名是真实技能
    if len(name) < 2 or name.isdigit():
        return True
    if _COMPOUND_NOISE.search(name):
        return True
    if _POSITION_NOISE.search(name):
        return True
    if _EXPERIENCE_NOISE.search(name):
        return True
    return False


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
            if norm in SKILL_WHITELIST or norm in SKILL_ALIAS:
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
    candidates = [(name, cnt) for name, cnt in counter.items() if not _is_noise(name)]
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
