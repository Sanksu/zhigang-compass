"""每日岗位重复对治理调度入口（2026-08-16 重复岗位对治理）。

被系统 cron / Windows 计划任务调用（06:45，图谱健康治理 06:30 之后）：
阶段 A 字符级变体/语义别名自动合并（--apply），阶段 B 语义近似对提议
（输出复核清单供人工确认别名）。每次先备份 reports/position_duplicates_*。

用法：
    python scripts/cron/position_dup_daily.py            # 全阶段 --apply
    python scripts/cron/position_dup_daily.py --dry-run  # 只报告（人工复核）
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.position_duplicate_cleanup import main as cleanup_main


def main() -> int:
    # 仅传 flags（程序名由 argparse 自身注入）
    argv = []
    if "--dry-run" not in sys.argv:
        argv.append("--apply")
    return cleanup_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
