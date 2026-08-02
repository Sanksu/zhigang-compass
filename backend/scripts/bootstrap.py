"""项目数据冷启动一键脚本。

从空库/新环境到可用图谱的编排入口：按依赖顺序调用现有初始化脚本，
任一阶段失败立即停止（fail-fast），全部阶段幂等可重跑。

顺序说明：
- init_neo4j → import_occupations → backfill（JD 抽取+课程入图）
- rebuild_graph 会清空图谱（保留 Counter），故课程入图须在 rebuild 之后重跑
- cleanup（技能过滤+岗位合并+聚合）→ 质量评估 → 发布首期版本快照

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
import subprocess
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

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
    ("evaluate", "课程质量评估（六维加权，写回 snapshot[quality]）",
     {"cmd": ["evaluate_courses"]}),
    ("snapshot", "发布图谱版本快照（graph_v{YYYYMMDD}，T+1 首期）",
     {"async": "snapshot"}),
]


def _run_subprocess(step_name: str, cmd: list[str]) -> None:
    """以子进程运行 `python -m scripts.xxx`（cwd=backend）。"""
    full_cmd = [sys.executable, "-m", "scripts." + cmd[0]] + cmd[1:]
    print(f"\n▶ [{step_name}] $ {full_cmd[-1]}")
    subprocess.run(
        full_cmd,
        cwd=str(_BACKEND_DIR),
        check=True,  # 非零退出码抛 CalledProcessError → fail-fast
    )


async def _run_async(step_name: str, target: str) -> None:
    """内联运行无独立 CLI 的异步阶段（load_courses / snapshot）。"""
    print(f"\n▶ [{step_name}] 异步任务 {target}")
    if target == "load_courses":
        from app.workers.tasks import load_courses

        result = await load_courses({})
    elif target == "snapshot":
        from app.services.evolution.graph_version import GraphVersionManager

        result = (await GraphVersionManager().create_snapshot(
            triggered_by="bootstrap"
        )).model_dump()
    else:
        raise ValueError(f"未知异步阶段: {target}")
    print(f"  → {result}")


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

    print("智岗罗盘数据冷启动开始")
    for name, desc, spec in _STEPS:
        if args.only and name != args.only:
            continue
        if name in skip:
            print(f"⏭  跳过阶段 {name}（--skip）")
            continue
        print(f"== [{name}] {desc} ==")
        try:
            if "cmd" in spec:
                _run_subprocess(name, spec["cmd"])
            else:
                asyncio.run(_run_async(name, spec["async"]))
        except subprocess.CalledProcessError as e:
            print(f"✗ 阶段 {name} 失败（退出码 {e.returncode}），已停止。修复后重跑（幂等）。")
            return e.returncode
        except Exception as e:
            print(f"✗ 阶段 {name} 失败: {e}")
            return 1
    print("\n冷启动全部完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
