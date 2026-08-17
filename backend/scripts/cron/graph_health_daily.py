"""每日图谱健康治理调度入口（2026-08-16 自动化）。

被系统 cron / Windows 计划任务调用（06:30，ETL 05:00 完成后）：
全阶段自动清理（阶段 A 同语言脏边 / B 孤立伪技能 / C 教学词），
每阶段先备份 reports/graph_health_* 再执行；SBERT 加载约 1 分钟。

用法：
    python scripts/cron/graph_health_daily.py            # 全阶段 --apply
    python scripts/cron/graph_health_daily.py --dry-run  # 只报告（人工复核）
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.graph_health_cleanup import main as cleanup_main


def main() -> int:
    # 仅传 flags（程序名由 argparse 自身注入）
    argv = []
    if "--dry-run" not in sys.argv:
        argv.append("--apply")
    return cleanup_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
