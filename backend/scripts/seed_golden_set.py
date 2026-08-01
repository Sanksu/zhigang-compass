"""黄金集导入 jd_raw 并触发 batch_extract 端到端验证（M3）。

流程：黄金集 100 条 upsert 到 jd_raw（幂等）→ batch_extract
（LLM 抽取 → snapshot[extraction] 写回 → kg_service.import_jd 入图）
→ 验证 jd_raw 抽取覆盖与 Neo4j 节点数。

用法：
    python scripts/seed_golden_set.py
    python scripts/seed_golden_set.py --limit 10   # 小批量冒烟
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.database import async_session_factory, neo4j_driver
from app.models.raw import JDRaw
from app.workers.tasks import batch_extract

_GOLDEN = _BACKEND_DIR / "data" / "golden_set" / "jd_golden_100.jsonl"


def _load_golden(limit: int | None) -> list[dict]:
    items = []
    with open(_GOLDEN, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                items.append(json.loads(line))
    return items[:limit] if limit else items


async def _seed(session, items: list[dict]) -> int:
    count = 0
    for item in items:
        snapshot = {
            "source": item.get("source", ""),
            "source_id": item.get("source_id", ""),
            "source_url": item.get("source_url", ""),
            "crawled_at": item.get("crawled_at", ""),
            "title": item.get("title", ""),
            "company": item.get("company", ""),
            "location": item.get("location", ""),
            "salary": item.get("salary", ""),
            "experience": item.get("experience", ""),
            "education": item.get("education", ""),
            "description": item.get("description", ""),
            "requirements": item.get("requirements", ""),
            "post_date": item.get("post_date", ""),
            "tags": item.get("tags", []),
            "is_desensitized": item.get("is_desensitized", True),
        }
        stmt = pg_insert(JDRaw).values(
            source=snapshot["source"],
            source_id=snapshot["source_id"],
            source_url=snapshot["source_url"],
            crawled_at=snapshot["crawled_at"],
            fingerprint=item.get("_fingerprint", ""),
            snapshot=snapshot,
            raw_text=item.get("raw_text", "")[:65535],
            is_desensitized=snapshot["is_desensitized"],
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_jd_raw_source_id",
            set_={
                "snapshot": stmt.excluded.snapshot,
                "raw_text": stmt.excluded.raw_text,
                "updated_at": func.now(),
            },
        )
        await session.execute(stmt)
        count += 1
    return count


async def _verify() -> None:
    async with async_session_factory() as session:
        total = await session.scalar(select(func.count()).select_from(JDRaw)) or 0
        extracted = await session.scalar(
            select(func.count()).select_from(JDRaw)
            .where(JDRaw.snapshot["extraction"].astext.isnot(None))
        ) or 0
    with neo4j_driver.session() as s:
        def _count(label: str) -> int:
            return s.run(f"MATCH (n:{label}) RETURN count(n) AS c").single()["c"]
        pos, sk, ev = _count("Position"), _count("Skill"), _count("Evidence")
    print(f"[verify] jd_raw={total} 已抽取={extracted} | Neo4j: Position={pos} Skill={sk} Evidence={ev}")


async def main(limit: int | None) -> None:
    items = _load_golden(limit)
    print(f"[seed] 黄金集 {len(items)} 条 → jd_raw（upsert 幂等）")
    async with async_session_factory() as session:
        seeded = await _seed(session, items)
        await session.commit()
    print(f"[seed] 完成 {seeded} 条")

    print("[extract] batch_extract 开始（LLM 真实调用，耗时取决于条数）")
    result = await batch_extract({}, limit=limit or 100)
    print(f"[extract] 结果: processed={result['processed']} succeeded={result['succeeded']} "
          f"failed={len(result['failed'])} positions={len(result['positions'])}")

    await _verify()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="黄金集导入 + batch_extract 端到端验证")
    parser.add_argument("--limit", type=int, default=None, help="仅处理前 N 条")
    args = parser.parse_args()
    asyncio.run(main(args.limit))
