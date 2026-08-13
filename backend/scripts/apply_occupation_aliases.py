"""occupations 别名桥接应用（2026-08-13，评审修复固化）。

JD 岗位名 → 大典职业的检索层映射（aliases 写入 occupations.hrss 条目）。
大典无前端/后端/测试等 JD 粒度职业，别名桥接让 RAG 接地命中正确职业。
本脚本幂等（按 code 合并别名，可重复执行）；执行后需同步 Neo4j
（import_occupations --source hrss --csv-dir 会同步，或手动跑 grounding 相关同步）。

用法：
    uv run python scripts/apply_occupation_aliases.py      # 应用别名 + 同步 Neo4j + 重算 embedding
"""

import asyncio
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("apply_occupation_aliases")

# 别名映射：大典职业 code → JD 岗位名别名列表（08-13 语义映射，经图谱岗位核验）
ALIAS_MAP = {
    "2-02-10-03": [  # 计算机软件工程技术人员
        "前端开发工程师", "Web前端工程师", "Vue前端工程师", "React前端工程师",
        "移动前端开发工程师", "后端开发工程师", "Java开发工程师", "Python开发工程师",
        "Go开发工程师", "C++开发工程师", "全栈工程师", "移动开发工程师",
        "Android开发工程师", "iOS开发工程师", "创始工程师",
        "测试工程师", "软件测试工程师", "QA", "测试开发工程师",
    ],
    "2-02-10-08": [  # 信息系统运行维护工程技术人员
        "运维工程师", "系统运维", "SRE", "数据库管理员", "DBA", "网络运维工程师", "网络工程师",
    ],
    "2-02-10-06": ["嵌入式开发工程师", "嵌入式软件工程师"],
    "2-02-10-07": ["网络安全工程师", "信息安全工程师", "渗透测试工程师"],
    "2-02-38-01": [  # 人工智能工程技术人员
        "大模型算法工程师", "机器学习工程师", "机器视觉工程师", "语音算法工程师",
        "自然语言处理工程师", "推荐搜索算法工程师", "算法工程师", "AI工程师",
        "自动驾驶算法工程师", "机器人算法工程师",
    ],
    "2-02-38-03": ["大数据开发工程师", "大数据工程师"],
    "2-02-38-04": ["DevOps工程师", "云平台工程师"],
    "2-02-30-09": [
        "数据科学家", "商业智能分析师", "量化分析师", "财务分析师",
        "市场分析师", "业务分析师", "精算分析师", "信贷分析师", "投资分析师", "首席统计师",
    ],
    "2-06-07-13": ["产品经理", "产品助理", "数字化运营", "数字化转型顾问"],
    "2-02-10-05": ["项目经理", "技术项目经理", "交付经理", "项目管理"],
}


async def main() -> int:
    from sqlalchemy import select

    from app.core.database import async_session_factory, neo4j_driver
    from app.models.business import Occupation

    async with async_session_factory() as db:
        updated = 0
        for code, aliases in ALIAS_MAP.items():
            occ = (await db.scalar(select(Occupation).where(Occupation.code == code, Occupation.source == "hrss")))
            if occ is None:
                logger.warning("code 未命中: %s", code)
                continue
            merged = list(dict.fromkeys([*(occ.aliases or []), *aliases]))
            if merged != occ.aliases:
                occ.aliases = merged
                updated += 1
        await db.commit()
        logger.info("别名应用完成: %s 个职业", updated)

        # 同步 Neo4j（aliases 参与全文索引）
        rows = (await db.scalars(select(Occupation).where(Occupation.source == "hrss"))).all()
        payload = [
            {"code": o.code, "name": o.name, "category": o.category,
             "definition": o.definition, "aliases": o.aliases}
            for o in rows
        ]
        with neo4j_driver.session() as ns:
            ns.run(
                "UNWIND $rows AS r MERGE (o:Occupation {code: r.code}) "
                "SET o.name = r.name, o.category = r.category, "
                "o.definition = r.definition, o.aliases = r.aliases",
                rows=payload,
            )
        logger.info("Neo4j 同步完成: %s 节点", len(payload))

    # 重算 embedding（别名参与向量化）
    from scripts.import_occupations import _fill_embeddings

    async with async_session_factory() as db:
        await _fill_embeddings(db)
    return updated


if __name__ == "__main__":
    n = asyncio.run(main())
    print(f"别名应用完成: {n} 个职业（Neo4j 同步 + embedding 重算）")
