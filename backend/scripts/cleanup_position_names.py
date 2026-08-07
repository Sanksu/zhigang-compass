"""清理图谱中带"高级"后缀的历史脏名岗位节点（一次性数据修复）。

背景：字典规则在加入"高级"尾部后缀剥离（_POSITION_SUFFIX_RE）之前，
岗位名如"全国重点客户高级经理" 会被归并为"全国重点客户高级"入图，
产生带"高级"后缀的脏名节点。字典更新后，这些名字经
normalize_position_name 可归并为目标名（如 → "全国重点客户"），但图谱
节点不会自动改名，且聚合端（jd_raw → normalize）按新规则产生的是目标名，
两者不一致导致聚合写回（write_aggregates 按 name MATCH）匹配不上。

处理范围：仅 name 含"高级"的节点（用户指定）：
- normalize_position_name 结果为空 → 删除（泛词无标准名）
- normalize 结果 != 原名 → 改名（若图谱已存在同名节点则报冲突，不自动合并）
- normalize 结果 == 原名（规则清理不了，如"激光高级工艺"）→ 跳过并提示

执行后从 jd_raw 重新聚合（freq/REQUIRES 重算），确保图谱与聚合端一致。

幂等：改名后节点不再含"高级"，重跑仅剩跳过项；重聚合天然幂等。

用法（容器内）：
    docker cp scripts/cleanup_position_names.py zhigang-api:/app/
    docker exec zhigang-api python /app/cleanup_position_names.py --dry-run
    docker exec zhigang-api python /app/cleanup_position_names.py
"""

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import select  # noqa: E402

from app.core.database import async_session_factory, neo4j_driver  # noqa: E402
from app.models.raw import JDRaw  # noqa: E402
from app.services.extraction.dictionary import normalize_position_name  # noqa: E402
from app.services.kg.aggregation import build_aggregates, write_aggregates  # noqa: E402


async def _load_jd_rows():
    async with async_session_factory() as s:
        return (await s.scalars(select(JDRaw))).all()


def main() -> None:
    parser = argparse.ArgumentParser(description="清理图谱带'高级'后缀的历史脏名岗位节点")
    parser.add_argument("--dry-run", action="store_true", help="仅报告，不修改")
    args = parser.parse_args()

    with neo4j_driver.session() as session:
        positions = session.run(
            "MATCH (p:Position) RETURN p.id AS id, p.name AS name, p.freq AS freq"
        ).data()

    rename, delete, skip = [], [], []
    for p in positions:
        name = p["name"]
        if "高级" not in name:
            continue
        std = normalize_position_name(name)
        if not std:
            delete.append(p)
        elif std != name:
            rename.append({**p, "std": std})
        else:
            skip.append(p)

    # 改名冲突防御：目标名与图谱现存节点重名（需人工合并，脚本不自动合并）
    existing = {p["name"] for p in positions}
    conflicts = [t for t in rename if t["std"] in existing]

    print(f"含'高级'节点: {len(rename) + len(delete) + len(skip)} 个")
    print(f"  改名: {len(rename)} 个")
    for t in rename:
        print(f"    {t['name']} → {t['std']}")
    print(f"  删除: {len(delete)} 个")
    for t in delete:
        print(f"    {t['name']} (freq={t['freq']})")
    print(f"  跳过(规则无法清理): {len(skip)} 个")
    for t in skip:
        print(f"    {t['name']}")
    if conflicts:
        print(f"\n!! 冲突（目标名已存在，需人工处理）: {[t['name'] + '→' + t['std'] for t in conflicts]}")
        return

    if args.dry_run:
        print("\n[dry-run] 未执行任何修改")
        return

    with neo4j_driver.session() as session:
        for t in rename:
            session.run("MATCH (p:Position {id: $id}) SET p.name = $std", id=t["id"], std=t["std"])
        for t in delete:
            session.run("MATCH (p:Position {id: $id}) DETACH DELETE p", id=t["id"])
        print(f"[1/2] 已改名 {len(rename)} 个、删除 {len(delete)} 个节点")

        rows = asyncio.run(_load_jd_rows())
        agg = build_aggregates(rows)
        now = datetime.now(timezone.utc).isoformat()
        result = write_aggregates(session, agg, now)
        print(f"[2/2] 重新聚合完成: 写入岗位 {result['positions']} 个、边 {result['edges']} 条")

    with neo4j_driver.session() as session:
        leftover = session.run(
            "MATCH (p:Position) WHERE p.name CONTAINS '高级' RETURN p.name AS name"
        ).data()
        total = session.run("MATCH (p:Position) RETURN count(p) AS c").single()["c"]
    print(f"清理后岗位总数: {total}  仍含'高级': {[r['name'] for r in leftover]}")


if __name__ == "__main__":
    main()
