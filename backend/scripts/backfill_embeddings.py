"""pgvector 三表向量回填（设计文档 §11.4.3）。

用法：
  python scripts/backfill_embeddings.py            # 全量回填三表
  python scripts/backfill_embeddings.py --jds      # 只回填 jd_embeddings
  python scripts/backfill_embeddings.py --no-skills --no-jds  # 只回填项目

幂等：skill/jd 按业务键 upsert，项目按 resume_id 先删后插，可安全重跑。
模型不可用时打印警告并跳过（不破坏已有数据）。
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.logging import setup_logging

logger = setup_logging("backfill_embeddings")

from app.core.database import async_session_factory
from app.services.embeddings.backfill import run_backfill
from app.services.matching.semantic import SemanticUnavailableError


async def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="pgvector 三表向量回填")
    parser.add_argument("--no-skills", action="store_true", help="跳过 skill_embeddings")
    parser.add_argument("--no-jds", action="store_true", help="跳过 jd_embeddings")
    parser.add_argument("--no-projects", action="store_true", help="跳过 project_embeddings")
    args = parser.parse_args()

    logger.info("开始 pgvector 三表向量回填")
    try:
        async with async_session_factory() as db:
            result = await run_backfill(
                db,
                skills=not args.no_skills,
                jds=not args.no_jds,
                projects=not args.no_projects,
            )
    except SemanticUnavailableError:
        logger.exception("语义模型不可用，回填跳过（SkillEmbedder 未加载）")
        return 1

    for table, stats in result.items():
        logger.info(f"{table}: {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
