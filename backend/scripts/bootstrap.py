"""项目数据冷启动一键脚本。

从空库/新环境到可用图谱的编排入口：按依赖顺序调用现有初始化脚本，
任一阶段失败立即停止（fail-fast），全部阶段幂等可重跑。

顺序说明：
- init_neo4j → import_occupations → backfill（JD 抽取+课程入图）
- rebuild_graph 会清空图谱（保留 Counter），故课程入图须在 rebuild 之后重跑
- cleanup（技能过滤+岗位合并+聚合）→ 证据关系迁移（MENTIONED_IN→EVIDENCED_BY）
  → 技能归一化（SIMILAR_TO）→ 质量评估 → 发布首期版本快照
- 快照之后补关系建边（PREREQUISITE_OF/BELONGS_TO/ALTERNATIVE_OF）、演化推导（EVOLVED_FROM）与 pgvector 向量回填，与每日 ETL 阶段 9.5/12.5/12.6/13 对齐

前置条件（本脚本不涉及，见 docs/冷启动指南.md）：
- docker compose up -d（postgres/redis/neo4j）+ alembic upgrade head
- 已通过爬虫采集 jd_raw / course_raw 原始数据

用法：
    uv run python scripts/bootstrap.py                # 全流程
    uv run python scripts/bootstrap.py --only backfill  # 只跑单个阶段
    uv run python scripts/bootstrap.py --skip cleanup    # 跳过指定阶段
"""

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.database import engine
from app.core.logging import setup_logging

logger = setup_logging("bootstrap")

# 阶段定义：(名称, 说明, 调用方式)
# subprocess 用 `python -m scripts.xxx`（cwd=backend，sys.path[0] 即 backend，可导入 app）
_STEPS: list[tuple[str, str, dict]] = [
    ("init_neo4j", "Neo4j schema 初始化（约束/索引/全文索引/Counter）",
     {"cmd": ["init_neo4j"]}),
    ("occupations", "O*NET 权威岗位库导入（1016 职业，幂等 upsert）",
     {"cmd": ["import_occupations"]}),
    ("backfill", "jd_raw 剩余 LLM 抽取 + course_raw 入图",
     {"cmd": ["backfill_ingest"]}),
    ("rebuild", "清空图谱并按新归一化规则重放已抽取 JD（保留 Counter）",
     {"cmd": ["rebuild_graph"]}),
    ("load_courses", "课程重新入图（rebuild 清空后恢复 Course/LEARNABLE_VIA）",
     {"async": "load_courses"}),
    ("cleanup", "技能过滤 + 岗位合并 + 重新聚合（防幻觉技能）",
     {"cmd": ["cleanup_graph"]}),
    ("evidence_relations", "证据关系迁移 MENTIONED_IN → EVIDENCED_BY（历史库命名对齐 §5.1）",
     {"cmd": ["migrate_evidence_relations"]}),
    ("skill_normalization", "技能归一化回写 + SIMILAR_TO 建边（ETL 阶段 9.5）",
     {"cmd": ["sync_skill_normalization"]}),
    ("evaluate", "课程质量评估（六维加权，写回 snapshot[quality]）",
     {"cmd": ["evaluate_courses"]}),
    ("snapshot", "发布图谱版本快照（graph_v{YYYYMMDD}，T+1 首期）",
     {"async": "snapshot"}),
    ("skill_relations", "技能关系建边：PREREQUISITE_OF/BELONGS_TO/ALTERNATIVE_OF（ETL 阶段 12.5）",
     {"cmd": ["sync_skill_relations"]}),
    ("evolved_from", "基于相邻快照推导 EVOLVED_FROM（ETL 阶段 12.6，首期无前序快照则跳过）",
     {"async": "evolved_from"}),
    ("backfill_embeddings", "pgvector 三表向量回填（ETL 阶段 13，模型不可用跳过）",
     {"async": "backfill_embeddings"}),
]


def _run_subprocess(step_name: str, cmd: list[str]) -> None:
    """以子进程运行 `python -m scripts.xxx`（cwd=backend）。"""
    full_cmd = [sys.executable, "-m", "scripts." + cmd[0]] + cmd[1:]
    logger.info("▶ [%s] $ %s", step_name, full_cmd[-1])
    subprocess.run(
        full_cmd,
        cwd=str(_BACKEND_DIR),
        check=True,  # 非零退出码抛 CalledProcessError → fail-fast
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )


async def _run_async(step_name: str, target: str) -> None:
    """内联运行无独立 CLI 的异步阶段（load_courses / snapshot / evolved_from / backfill_embeddings）。"""
    logger.info("▶ [%s] 异步任务 %s", step_name, target)
    if target == "load_courses":
        from app.workers.tasks import load_courses

        result = await load_courses({})
    elif target == "snapshot":
        from app.services.evolution.graph_version import GraphVersionManager

        result = (await GraphVersionManager().create_snapshot(
            triggered_by="bootstrap"
        )).model_dump()
    elif target == "evolved_from":
        from app.services.evolution.evolved_from import derive_evolved_from

        result = await derive_evolved_from()
    elif target == "backfill_embeddings":
        from app.workers.tasks import backfill_embeddings

        result = await backfill_embeddings({})
    else:
        raise ValueError(f"未知异步阶段: {target}")
    logger.info("  → %s", result)
    # Windows 下 asyncpg 连接绑定创建时的事件循环；每个异步阶段独立 asyncio.run()（新 loop），
    # 若不释放连接池，下一阶段会复用上一 loop 的连接导致 `_proactor` 为 None 崩溃（ProactorEventLoop 坑）
    await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="智岗罗盘数据冷启动一键脚本")
    parser.add_argument(
        "--only", help="只运行指定阶段（如 backfill / snapshot，见 --list）"
    )
    parser.add_argument(
        "--skip", help="跳过指定阶段（逗号分隔，如 cleanup,snapshot）"
    )
    parser.add_argument(
        "--list", action="store_true", help="列出全部阶段"
    )
    args = parser.parse_args()

    if args.list:
        for name, desc, _ in _STEPS:
            print(f"  {name:<14} {desc}")
        return 0

    skip = {s.strip() for s in (args.skip or "").split(",") if s.strip()}

    logger.info("智岗罗盘数据冷启动开始")
    for name, desc, spec in _STEPS:
        if args.only and name != args.only:
            continue
        if name in skip:
            logger.info("⏭ 跳过阶段 %s（--skip）", name)
            continue
        logger.info("== [%s] %s ==", name, desc)
        try:
            if "cmd" in spec:
                _run_subprocess(name, spec["cmd"])
            else:
                asyncio.run(_run_async(name, spec["async"]))
        except subprocess.CalledProcessError as e:
            logger.error("✗ 阶段 %s 失败（退出码 %s），已停止。修复后重跑（幂等）。", name, e.returncode)
            return e.returncode
        except Exception as e:
            logger.exception("✗ 阶段 %s 失败", name)
            return 1
    logger.info("冷启动全部完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
