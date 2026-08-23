"""本地 dry-run：真实数据验证 discovery_auto_transition 判定逻辑（不落库）。

用 jd_raw 已抽取记录按 post_date 聚合 30 天窗口发布频次，对候选池中
emerging/stable/declining 岗位运行 evaluate_auto_transition，打印每个岗位的
完整信号链（窗口序列 / z-scores / 波动率 / 下降率 / 判定结果），便于排查
declining 信号是否准确。只读，不调用 persist、不写库。

用法：
    uv run python scripts/dryrun_discovery_transition.py
"""

import asyncio
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from sqlalchemy import select

from app.core.logging import setup_logging

logger = setup_logging("dryrun_discovery_transition")

from app.core.database import async_session_factory
from app.models.business import DiscoveryCandidate
from app.models.raw import JDRaw
from app.services.discovery.schemas import CandidatePosition, DiscoveryFeatures, PositionState
from app.services.discovery.state_machine import (
    WindowFreq,
    decline_rate,
    evaluate_auto_transition,
    freq_z_scores,
    jd_publish_windows,
    window_volatility,
)
from app.services.extraction.dictionary import normalize_position_name
from app.workers.tasks import _first_seen_date_of


async def main() -> None:
    # ── 1. jd_raw → 岗位按天发布数 → 30 天窗口序列 ──
    async with async_session_factory() as session:
        jd_rows = (await session.scalars(
            select(JDRaw).where(JDRaw.snapshot["extraction"].astext.isnot(None))
        )).all()

    daily_freqs: dict[str, dict[str, int]] = {}
    for row in jd_rows:
        ext = (row.snapshot or {}).get("extraction") or {}
        name = normalize_position_name(ext.get("position_name") or "")
        if not name:
            continue
        day = _first_seen_date_of(row)
        day_counts = daily_freqs.setdefault(name, {})
        day_counts[day] = day_counts.get(day, 0) + 1

    freq_windows = jd_publish_windows(daily_freqs)
    logger.info("jd_raw 已抽取记录: %s，有窗口序列的岗位: %s", len(jd_rows), len(freq_windows))

    # ── 2. 候选池自动可迁移岗位判定（只读）──
    async with async_session_factory() as session:
        rows = (await session.scalars(
            select(DiscoveryCandidate).where(
                DiscoveryCandidate.state.in_(
                    [PositionState.EMERGING.value, PositionState.STABLE.value, PositionState.DECLINING.value]
                )
            )
        )).all()
    logger.info("候选池自动可迁移状态岗位: %s", len(rows))

    n_skip = n_transition = 0
    for row in rows:
        name = normalize_position_name(row.position_name)
        if not name:
            continue
        freqs = freq_windows.get(name, [])
        if len(freqs) < 2:
            logger.info(
                "  [跳过] %s 窗口序列 %s（<2 期，冷启动不武断判定）",
                row.position_name, freqs,
            )
            n_skip += 1
            continue

        features = DiscoveryFeatures(**row.features)
        candidate = CandidatePosition(
            candidate_id=row.id,
            position_name=row.position_name,
            state=PositionState(row.state),
            features=features,
            detected_at=row.detected_at,
        )
        conf = float((row.confidence or {}).get("final_confidence", 0.0))
        windows = WindowFreq(freqs=freqs, z_scores=freq_z_scores(freqs))
        target = evaluate_auto_transition(candidate, windows, confidence=conf)
        logger.info(
            "  %s state=%s 窗口=%s z=%s volatility=%.3f decline_rate=%.3f "
            "source_diversity=%s → %s",
            row.position_name, row.state, freqs,
            [round(z, 3) for z in windows.z_scores],
            window_volatility(windows), decline_rate(windows),
            features.source_diversity, target.value if target else "不迁移",
        )
        if target is not None:
            n_transition += 1

    logger.info("汇总: 跳过 %s，判定迁移 %s", n_skip, n_transition)


if __name__ == "__main__":
    asyncio.run(main())
