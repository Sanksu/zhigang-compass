# -*- coding: utf-8 -*-
"""未分类技能批量 LLM 分类提议（人工触发批通道，补每日任务覆盖缺口）。

背景（2026-08-24 线上盘点）：Skill 分类覆盖率 12.8%（564/4399），且未分类
集中在中高频核心概念（度数≥20 有 214 个）；每日 ETL 审查
（workers/skill_category_review.py：req_count 升序、≤20 条/日、触发门
req_count≤3）按设计只扫长尾，高频永远轮不到。本脚本按 **req_count 降序**
取 top-N 并发提议，一次跑平高频欠账。

与每日任务同一权威边界：只写 suggested_category* 提议字段，**不动权威
category**；晋升 = 高置信清单经算法岗审查后进 configs/skill_whitelist.yaml
（白名单同时是抽取第三道防线合法词表，扩容即改变抽取行为，必须人工审）。

用法：
    uv run python scripts/batch_skill_category_propose.py --limit 600
    uv run python scripts/batch_skill_category_propose.py --dry-run --limit 20
报告：reports/skill_category_batch_{date}.json（含按类目分组的晋升候选）
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("batch_skill_category_propose")

_CST = timezone(timedelta(hours=8))
_REPORT_DIR = _BACKEND_DIR / "reports"

# 晋升置信分档：< HIGH 落入 needs_review 清单，不自动进白名单草案
HIGH_CONFIDENCE = 0.70


def _fetch_unclassified(session, limit: int, min_req_count: int) -> list[dict]:
    """未分类技能按引用数降序（与每日任务相反方向：先还高频的债）。"""
    query = (
        "MATCH (s:Skill) "
        "OPTIONAL MATCH (s)<-[r:REQUIRES]-() "
        "WITH s, count(r) AS req_count "
        "WHERE (s.category IS NULL OR s.category = '' OR s.category = '未分类') "
        "  AND req_count >= $min_req_count "
        "RETURN s.name AS name, req_count, s.suggested_category AS suggested_category "
        "ORDER BY req_count DESC, s.name LIMIT $limit"
    )
    return [dict(record) for record in session.run(
        query, limit=limit, min_req_count=min_req_count,
    )]


def select_candidates(rows: list[dict], refresh: bool) -> list[dict]:
    """去重（同名节点取引用数最高的一条）并过滤已有提议（refresh 时重提）。"""
    best: dict[str, dict] = {}
    for row in rows:
        name = row.get("name") or ""
        if not name.strip():
            continue
        prev = best.get(name)
        if prev is None or (row.get("req_count") or 0) > (prev.get("req_count") or 0):
            best[name] = row
    if refresh:
        return sorted(best.values(), key=lambda r: -(r.get("req_count") or 0))
    return sorted(
        (r for r in best.values() if not r.get("suggested_category")),
        key=lambda r: -(r.get("req_count") or 0),
    )


def propose_batch(candidates: list[dict], classify, workers: int = 6) -> tuple[list[dict], list[str]]:
    """并发分类；classify 注入便于测试。返回 (提议成功清单, 失败名单)。"""
    classified: list[dict] = []
    failed: list[str] = []

    def _one(row: dict) -> None:
        name = row["name"]
        try:
            result = classify(name)
        except Exception as e:  # noqa: BLE001 - 单条失败不阻塞批次
            logger.warning("分类异常 %s: %s", name, e)
            failed.append(name)
            return
        if result is None:
            failed.append(name)
            return
        classified.append({
            "name": name,
            "category": result.category,
            "confidence": round(float(result.confidence), 4),
            "reason": result.reason,
            "req_count": int(row.get("req_count") or 0),
        })

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(_one, row) for row in candidates]
        for fut in as_completed(futures):
            fut.result()
    classified.sort(key=lambda c: -c["req_count"])
    return classified, failed


def build_promotion(classified: list[dict]) -> dict:
    """按类目分组产出晋升候选：高置信直接进草案，低置信留人工复核。"""
    high: dict[str, list[dict]] = {}
    review: dict[str, list[dict]] = {}
    for item in classified:
        bucket = high if item["confidence"] >= HIGH_CONFIDENCE else review
        bucket.setdefault(item["category"], []).append(item)
    return {
        "high_confidence": {k: sorted(v, key=lambda i: -i["req_count"]) for k, v in sorted(high.items())},
        "needs_review": {k: sorted(v, key=lambda i: -i["req_count"]) for k, v in sorted(review.items())},
    }


def write_suggestions(session, classified: list[dict], run_date: str) -> None:
    """提议字段批量写回（UNWIND 单事务）。"""
    if not classified:
        return
    session.run(
        """
        UNWIND $rows AS row
        MATCH (s:Skill {name: row.name})
        SET s.suggested_category = row.category,
            s.suggested_category_confidence = row.confidence,
            s.suggested_category_reason = row.reason,
            s.suggested_category_at = $at
        """,
        rows=classified,
        at=run_date,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="未分类技能批量 LLM 分类提议")
    parser.add_argument("--limit", type=int, default=600, help="按引用数取前 N 个")
    parser.add_argument("--min-req-count", type=int, default=1, help="引用数下限")
    parser.add_argument("--workers", type=int, default=6, help="LLM 并发数")
    parser.add_argument("--refresh", action="store_true", help="对已有提议的技能重新提议")
    parser.add_argument("--dry-run", action="store_true", help="只列候选不调 LLM 不写库")
    args = parser.parse_args()

    from app.core.database import neo4j_driver

    with neo4j_driver.session() as session:
        rows = _fetch_unclassified(session, args.limit, args.min_req_count)
    candidates = select_candidates(rows, refresh=args.refresh)
    logger.info("未分类池 %d → 候选 %d（refresh=%s）", len(rows), len(candidates), args.refresh)
    if args.dry_run:
        for row in candidates[:30]:
            logger.info("  候选 %-40s req_count=%s", row["name"], row.get("req_count"))
        return

    from app.services.extraction.llm_provider import LLMConfigurationError, LLMProviderChain
    from app.services.extraction.skill_category_review import classify_skill

    try:
        llm = LLMProviderChain()
    except LLMConfigurationError:
        logger.error("LLM 未配置，无法提议（llm_providers.yaml 检查）")
        sys.exit(2)

    classified, failed = propose_batch(candidates, lambda name: classify_skill(name, llm),
                                       workers=args.workers)

    run_date = datetime.now(_CST).strftime("%Y-%m-%d")
    with neo4j_driver.session() as session:
        write_suggestions(session, classified, run_date)

    report = {
        "run_date": run_date,
        "pool": len(rows),
        "candidates": len(candidates),
        "proposed": len(classified),
        "failed": failed,
        "promotion": build_promotion(classified),
    }
    path = _REPORT_DIR / f"skill_category_batch_{run_date}.json"
    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(
        "提议完成：%d/%d 成功（失败 %d），高置信 %d 条；报告 %s",
        len(classified), len(candidates), len(failed),
        sum(len(v) for v in report["promotion"]["high_confidence"].values()),
        path,
    )


if __name__ == "__main__":
    main()
