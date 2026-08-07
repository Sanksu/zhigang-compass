"""回填 jd_raw 国内源详情正文（zhilian）。

背景：zhilian 列表页不返回正文（详情页有验证码反爬），采集时 description/
requirements 为空，raw_text 仅为元数据摘要（平均 117 字），LLM 抽取的
"职责描述"质量差。本脚本遍历正文为空的存量记录，抓取详情页 SSR 正文回填。

- zhilian：详情页 __INITIAL_STATE__ 解析（无需登录态，8-15s 随机间隔）
- 回填后删除 snapshot.extraction 标记（batch_extract 依据该键缺失判定未抽取），
  下次触发数据入图（run_ingest / batch_extract）时用完整正文重新抽取
- 幂等：仅处理 description/requirements 均为空的记录，中断后可续跑

用法：
    uv run python scripts/backfill_jd_detail.py --dry-run --limit 20
    uv run python scripts/backfill_jd_detail.py --limit 100
    uv run python scripts/backfill_jd_detail.py             # 全量回填
"""

import argparse
import asyncio
import random
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))
sys.path.insert(0, str(_BACKEND_DIR / "data"))  # crawlers 包

import httpx
from sqlalchemy import select, update

from app.core.database import async_session_factory
from app.models.raw import JDRaw
from crawlers.zhilian_detail import extract_job_detail

# 与浏览器一致的 UA（绕过默认库 UA 被拒）
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")
# 平台限速间隔（对齐 crawlers.settings.RATE_LIMIT：zhilian 8-15s）
_DELAY_RANGE = (8, 15)
_DETAIL_TIMEOUT = 20


async def _pending_rows(limit: int) -> list[tuple[int, str, str, dict]]:
    """正文为空的存量记录（id, source_id, raw_text, snapshot），不持有 session。"""
    async with async_session_factory() as session:
        stmt = (
            select(JDRaw.id, JDRaw.source_id, JDRaw.raw_text, JDRaw.snapshot)
            .where(
                JDRaw.source == "zhilian",
                (JDRaw.snapshot["description"].astext == "")
                | (JDRaw.snapshot["description"].astext.is_(None)),
            )
            .order_by(JDRaw.id)
        )
        if limit > 0:
            stmt = stmt.limit(limit)
        return [
            (r.id, r.source_id, r.raw_text or "", dict(r.snapshot or {}))
            for r in await session.execute(stmt)
        ]


async def _apply(row_id: int, snapshot: dict, raw_text: str) -> None:
    """按 id 写回 snapshot 与 raw_text（短 session，天然支持断点续跑）。"""
    async with async_session_factory() as session:
        await session.execute(
            update(JDRaw)
            .where(JDRaw.id == row_id)
            .values(snapshot=snapshot, raw_text=raw_text)
        )
        await session.commit()


async def _fetch_zhilian_detail(client: httpx.AsyncClient, source_id: str) -> dict:
    """抓取 zhilian 详情页并解析正文。source_id 即详情页 URL 中的 number。"""
    url = f"https://www.zhaopin.com/jobdetail/{source_id}.htm"
    resp = await client.get(url, headers={"User-Agent": _UA}, timeout=_DETAIL_TIMEOUT)
    resp.raise_for_status()
    return extract_job_detail(resp.text)


async def backfill_zhilian(limit: int, dry_run: bool, keep_extraction: bool) -> dict:
    rows = await _pending_rows(limit)
    print(f"待回填 {len(rows)} 条（limit={limit or '全量'} dry_run={dry_run}）")
    if dry_run:
        return {"updated": 0, "failed": [], "pending": len(rows)}

    updated = 0
    failed: list[dict] = []
    async with httpx.AsyncClient(
        timeout=_DETAIL_TIMEOUT, follow_redirects=True
    ) as client:
        for idx, (row_id, source_id, raw_text, orig_snapshot) in enumerate(rows, 1):
            try:
                detail = await _fetch_zhilian_detail(client, source_id)
            except Exception as e:
                failed.append({"id": row_id, "error": str(e)[:200]})
                print(f"[{idx}/{len(rows)}] id={row_id} 详情抓取失败: {e}")
                continue

            if not (detail["description"] or detail["requirements"]):
                failed.append({"id": row_id, "error": "详情页无正文（SSR 缺失）"})
                print(f"[{idx}/{len(rows)}] id={row_id} 详情页无正文，跳过")
                continue

            # 正文回填：description/requirements 更新，raw_text 追加正文（兜底备份）
            snapshot = dict(orig_snapshot)
            snapshot["description"] = detail["description"]
            snapshot["requirements"] = detail["requirements"]
            if not keep_extraction:
                # 清除旧抽取标记，下次 batch_extract 按完整正文重新抽取
                snapshot.pop("extraction", None)
            await _apply(
                row_id, snapshot,
                "\n".join([raw_text, detail["description"], detail["requirements"]]).strip("\n"),
            )
            updated += 1
            print(
                f"[{idx}/{len(rows)}] id={row_id} 回填成功 "
                f"(desc={len(detail['description'])} req={len(detail['requirements'])})"
            )

            await asyncio.sleep(random.uniform(*_DELAY_RANGE))

    print(f"完成：更新 {updated} 条，失败 {len(failed)} 条")
    return {"updated": updated, "failed": failed, "pending": len(rows)}


def main():
    parser = argparse.ArgumentParser(description="回填 zhilian 详情正文到 jd_raw")
    parser.add_argument("--limit", type=int, default=0, help="回填条数上限（0=全部正文为空记录）")
    parser.add_argument("--dry-run", action="store_true", help="仅统计待回填条数，不写库")
    parser.add_argument("--keep-extraction", action="store_true",
                        help="保留旧 extraction 标记（仅补正文，不触发下次重抽）")
    args = parser.parse_args()
    asyncio.run(backfill_zhilian(args.limit, args.dry_run, args.keep_extraction))


if __name__ == "__main__":
    main()
