"""大典职业 definition 补全（2026-08-13，评审 P0-2）。

OSTA 采集的大典职业仅有名称层级（无定义字段）——RAG 接地的定义密度不足。
本脚本对**图谱高频岗位的映射目标职业**（EXPECTED 映射的目标，约 25 个）
用 LLM 批量生成中文职业定义（1-3 句，JSON Schema 强校验），写回
occupations.definition（hrss 源），随后重算 embedding + 同步 Neo4j
（供语义路与全文路共同增强）。

定义生成语义：依据大典职业名 + 别名（JD 岗位名桥接）凝练——LLM 失败跳过
不阻塞（后续可重跑）。

用法：
    uv run python scripts/backfill_occupation_definitions.py          # 补全映射目标职业
    uv run python scripts/backfill_occupation_definitions.py --all    # 补全全部 hrss 空定义职业
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("backfill_occupation_definitions")

# 图谱高频岗位 → 大典职业映射目标（优先补全这些职业的定义）
TARGET_CODES = [
    "2-02-10-03",  # 计算机软件工程技术人员（前端/后端/Java/Python/全栈/移动/测试）
    "2-02-38-01",  # 人工智能工程技术人员（大模型/算法/视觉/语音/机器人）
    "2-02-30-09",  # 数据分析处理工程技术人员（数据分析/数据科学）
    "2-02-38-03",  # 大数据工程技术人员
    "2-02-38-04",  # 云计算工程技术人员（DevOps）
    "2-02-10-07",  # 信息安全工程技术人员（网络安全）
    "2-02-10-06",  # 嵌入式系统设计工程技术人员
    "2-02-10-08",  # 信息系统运行维护工程技术人员（运维/DBA/网络）
    "2-06-07-13",  # 数字化管理师（产品经理）
    "2-02-10-05",  # 信息系统分析工程技术人员（项目经理）
]

_DEFINITION_PROMPT = """你是职业分类专家。根据职业名称与别名，用中文撰写简洁的职业定义（1-3 句话，60-120 字）。

职业名称：{name}
别名（JD 岗位名常见写法）：{aliases}

要求：
- 描述该职业的职责范围与技术领域
- 涵盖别名对应的常见工作内容
- 只输出定义本身

输出 JSON：{{"definition": "..."}}"""


async def backfill(target_codes: list[str] | None, all_empty: bool) -> int:
    import json as _json

    from sqlalchemy import select

    from app.core.database import async_session_factory, neo4j_driver
    from app.models.business import Occupation
    from app.services.extraction.llm_provider import LLMProviderChain
    from pydantic import BaseModel

    class _DefOut(BaseModel):
        definition: str

    async with async_session_factory() as db:
        stmt = select(Occupation).where(Occupation.source == "hrss")
        if all_empty:
            stmt = stmt.where(Occupation.definition == "")
        elif target_codes:
            stmt = stmt.where(Occupation.code.in_(target_codes))
        rows = (await db.scalars(stmt)).all()
        logger.info("待补全职业: %s 个", len(rows))

        llm = LLMProviderChain()
        updated = 0
        for occ in rows:
            if occ.definition and not all_empty:
                continue
            aliases = ";".join(occ.aliases or []) if occ.aliases else ""
            prompt = _DEFINITION_PROMPT.format(name=occ.name, aliases=aliases or "无")
            try:
                out = await asyncio.to_thread(llm.call_with_fallback, prompt, _DefOut)
                definition = out.definition.strip()
                if definition:
                    occ.definition = definition
                    updated += 1
                    if updated % 10 == 0:
                        await db.commit()
            except Exception as e:
                logger.warning("定义生成失败 %s: %s", occ.name, str(e)[:80])
        await db.commit()
        logger.info("定义补全完成: %s 条", updated)

        # 同步 Neo4j（definition 参与全文索引）
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
        logger.info("Neo4j 同步完成")

    # 重算 embedding（定义参与向量化，评审 P1-1）——全量 hrss 刷新
    from scripts.import_occupations import _fill_embeddings

    async with async_session_factory() as db:
        await _fill_embeddings(db)
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description="大典职业定义补全（LLM 生成）")
    parser.add_argument("--all", action="store_true", help="补全全部 hrss 空定义职业（默认仅映射目标）")
    args = parser.parse_args()
    n = asyncio.run(backfill(None if args.all else TARGET_CODES, args.all))
    print(f"定义补全完成: {n} 条")


if __name__ == "__main__":
    main()
