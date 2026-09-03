"""数据更新新鲜度与 T+1 合规检查（DA-M4-03）。

设计文档承诺：每日 04:00 ETL 完成、05:00 前发布新版本（T+1 图谱更新）。
本模块对四类 raw 表按来源聚合最新抓取时间，判定平台级新鲜度，
使更新机制可审计、可告警（cron 可依据退出码告警）。

所有函数为纯函数（输入 dict 列表），便于单测与真实数据复用。
"""

from datetime import datetime, timedelta, timezone

# T+1 承诺阈值：某来源数据距今天数 ≤ 1 天视为新鲜（每日发布）
_T1_DAYS = 1.0
_TZ_CN = timezone(timedelta(hours=8))


def _freshness_threshold(source: str) -> float:
    """按来源取新鲜度阈值（统一 T+1 日级）。

    课程源（coursera/edx/icourse163）08-28 已并入主管线**按日采集**
    （etl.py course_platforms），故不再按周更放宽——否则断更 7 天才会告警，
    与日级闭环口径不一致（原 _WEEKLY_SOURCES 周更假设于 08-21 TODO-CRS-01
    记录、08-28 拍板按日采集后移除）。
    """
    return _T1_DAYS


def parse_crawled_at(value: str | None) -> datetime | None:
    """解析 crawled_at（带时区 ISO 格式）；无法解析返回 None。"""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    # 无时区视为 UTC（采集侧默认 UTC）
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _days_since(dt: datetime, now: datetime) -> float:
    return (now - dt).total_seconds() / 86400.0


def platform_freshness(rows: list[dict], now: datetime | None = None) -> dict:
    """按来源聚合最新抓取时间与新鲜度。

    rows: [{source, crawled_at}] → {
        platforms: [{source, last_crawl, days_since, fresh}],
        stale_sources: [source, ...],
        t1_compliant: bool
    }
    fresh = 距今天数 ≤ _T1_DAYS（T+1 每日发布承诺）。
    crawled_at 无法解析的来源视为不新鲜（不静默放行）。
    """
    now = now or datetime.now(_TZ_CN)
    seen_sources: set[str] = set()
    latest: dict[str, datetime] = {}
    for r in rows:
        source = (r.get("source") or "").strip()
        if not source:
            continue
        seen_sources.add(source)
        dt = parse_crawled_at(r.get("crawled_at"))
        if dt is None:
            continue
        if source not in latest or dt > latest[source]:
            latest[source] = dt

    platforms = []
    stale = []
    for source in sorted(seen_sources):
        dt = latest.get(source)
        if dt is None:
            stale.append(source)
            platforms.append({
                "source": source, "last_crawl": None, "days_since": None, "fresh": False,
            })
            continue
        days = round(_days_since(dt, now), 2)
        fresh = days <= _freshness_threshold(source)
        platforms.append({
            "source": source,
            "last_crawl": dt.isoformat(),
            "days_since": days,
            "fresh": fresh,
        })
        if not fresh:
            stale.append(source)

    platforms.sort(key=lambda p: (p["days_since"] is None, -(p["days_since"] or 0)))
    return {
        "platforms": platforms,
        "stale_sources": stale,
        "t1_compliant": not stale,
        "t1_days": _T1_DAYS,
    }
