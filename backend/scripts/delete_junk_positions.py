# -*- coding: utf-8 -*-
"""垃圾岗位一次性清理（2026-08-31 岗位域治理）。

背景：linkedin_public/indeed/glassdoor 国际源单条 JD 产出的岗位中混入
（a）雇主名被抽成岗位名（Clay/GSBOA/Corebridge/Hannaford/Medbio）；
（b）非技术职业岗（卡车司机/前台/技工/药剂师/销售/BD 等）。
全部为 freq≤5 低频单例，却把岗位职能域聚类拖垮——「项目管理」域 15 席中
13 席为垃圾岗、「系统可靠性」域因相邻垃圾画像错聚（TypeScript工程师案例）。

为什么不走 cleanup_graph.py 全量清理：该脚本按「归一化为空串即删除」执行，
会把历史快照产出的「算法工程师」(freq=97，失真兜底族路由规则前入图) 一并
删除，触发 97 条 JD 的再分配迁移——那是独立工作流（backfill_normalized_positions），
不属于本治理口径。本脚本按显式清单外科手术式删除。

防复发由 dictionary 停用词承接（_COMPANY_NAME_STOPWORDS /
_POSITION_STOPWORDS，同 PR 扩充）；审计：jd_raw（PG）保留全部原始证据，
删除仅影响图谱聚合视图。

安全门禁：
- 默认 dry-run，--apply 才执行；
- 每条目标核对 freq ≤ _MAX_FREQ 且 status 为 active/emerging/stable/declining，
  不符即跳过并告警（防止清单过期误删已长大的岗位）；
- 命中 PositionEditLog（人工编辑痕迹）的岗位跳过；
- 图内不存在的岗位视为已处理，静默跳过（幂等可重跑）；
- 删除前备份 reports/deleted_junk_positions_{date}.jsonl。

用法（cwd=backend）：
    python -m scripts.delete_junk_positions           # dry-run 报告
    python -m scripts.delete_junk_positions --apply   # 执行删除
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core.database import neo4j_driver
from app.core.logging import setup_logging

logger = setup_logging("delete_junk_positions")

# 删除上限：清单内岗位均为低频单例；freq 超限说明清单过期（岗位已长大），拒绝删除
_MAX_FREQ = 5
# 允许删除的状态（legacy/rejected 不在公开视图，保留审计痕迹不动）
_DELETABLE_STATUSES = ("active", "emerging", "stable", "declining")

# 显式清单：岗位名 → 删除原因（2026-08-31 岗位域治理逐条人工确认）
JUNK_POSITIONS: dict[str, str] = {
    # —— 雇主名被抽成岗位名 ——
    "Clay": "公司名当岗位名（linkedin_public）",
    "GSBOA": "公司名当岗位名（linkedin_public）",
    "Corebridge": "公司名当岗位名（indeed）",
    "Hannaford": "公司名当岗位名（indeed）",
    "Medbio": "公司名当岗位名（linkedin_public）",
    # —— 非技术职业岗（国际源标题过滤漏网）——
    "Receptionist": "非技术职业（前台）",
    "Clerk": "非技术职业（柜员/文书）",
    "Installer": "非技术职业（安装工）",
    "Electrician": "非技术职业（电工）",
    "Locksmith": "非技术职业（锁匠）",
    "Pharmacist": "非技术职业（药剂师）",
    "SeniorMeatCutter": "非技术职业（肉类切割工）",
    "Expeditor": "非技术职业（跟单催货员）",
    "QC": "非技术职业（质检）",
    "IPQC": "非技术职业（制程质检）",
    "B级卡车司机": "非技术职业（司机）",
    "CDL-A司机": "非技术职业（司机）",
    "CDL-A卡车司机": "非技术职业（司机，与 CDL-A司机 同岗异形）",
    "SAT辅导教师": "非技术职业（培训教师）",
    "CNC机械师": "非技术职业（机械操作）",
    "CNC路由器操作员": "非技术职业（机械操作）",
    "房地产合作与批量MDU销售副总裁": "非技术职业（地产销售）",
    "APP推广": "非技术职业（推广运营）",
    "ECMO项目协调员": "非技术职业（医疗设备协调）",
    "START协调员": "非技术职业（项目协调）",
    "市场营销与CRM": "非技术职业（市场营销）",
    "行业BD": "非技术职业（商务拓展）",
    "GTM商务拓展": "非技术职业（商务拓展）",
    "IT": "无信息量泛词（与「技术」「开发」同口径，2026-08-31 治理时清单漏列补录）",
    "AI提示词": "碎片岗（「提示词」停用词的复合形漏网；图谱实证其 JD 实为后端向 AI 应用岗）",
}


def _load_targets() -> tuple[list[dict], list[dict]]:
    """核对清单内岗位的图内实况，返回 (可删清单, 跳过清单)。"""
    deletable, skipped = [], []
    with neo4j_driver.session() as session:
        for name, reason in JUNK_POSITIONS.items():
            row = session.run(
                """
                MATCH (p:Position {name: $name})
                OPTIONAL MATCH (l:PositionEditLog {position_name: $name})
                RETURN p.id AS id, p.freq AS freq, p.status AS status,
                       count(l) AS edits
                """,
                name=name,
            ).single()
            if row is None or row["id"] is None:
                continue  # 图内不存在：已处理或 226 口径差异，幂等跳过
            freq = row["freq"] or 0
            status = row["status"] or "active"
            if freq > _MAX_FREQ:
                skipped.append({"name": name, "reason": reason, "why": f"freq={freq} > {_MAX_FREQ}，清单过期"})
                continue
            if status not in _DELETABLE_STATUSES:
                skipped.append({"name": name, "reason": reason, "why": f"status={status} 保留审计痕迹"})
                continue
            if row["edits"]:
                skipped.append({"name": name, "reason": reason, "why": "存在 PositionEditLog 人工编辑痕迹"})
                continue
            deletable.append({"name": name, "id": row["id"], "freq": freq,
                              "status": status, "reason": reason})
    return deletable, skipped


def _backup(rows: list[dict]) -> Path:
    date = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = ROOT / "reports" / f"deleted_junk_positions_{date}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    logger.info("备份: %s（%s 条）", path, len(rows))
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="垃圾岗位一次性清理（显式清单）")
    parser.add_argument("--apply", action="store_true", help="执行删除（默认 dry-run 只报告+备份）")
    args = parser.parse_args(argv)

    deletable, skipped = _load_targets()
    logger.info("=" * 60)
    logger.info("垃圾岗位清理 %s：可删 %d，跳过 %d，清单 %d",
                "[执行]" if args.apply else "[dry-run]", len(deletable), len(skipped), len(JUNK_POSITIONS))
    for s in skipped:
        logger.warning("  跳过 %s：%s", s["name"], s["why"])
    if not deletable:
        logger.info("无可删目标")
        return 0
    _backup(deletable + skipped)
    if not args.apply:
        for d in deletable:
            logger.info("  [待删] freq=%d %s —— %s", d["freq"], d["name"], d["reason"])
        logger.info("(dry-run，--apply 才执行)")
        return 0

    with neo4j_driver.session() as session:
        session.run(
            """
            UNWIND $ids AS nid
            MATCH (p:Position {id: nid})
            DETACH DELETE p
            """,
            ids=[d["id"] for d in deletable],
        )
    logger.info("已删除 %d 个垃圾岗位（证据原文保留于 PG jd_raw）", len(deletable))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
