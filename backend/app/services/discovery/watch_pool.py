"""技术热点观察池信号聚合（设计文档 7.2.5 趋势监测）。

从 raw 表（jd_raw / course_raw / paper_raw / community_raw）按周聚合技能
信号，判定是否命中条件监测矩阵阈值：

| 源       | 判定阈值                        | signal_value |
|----------|--------------------------------|--------------|
| jd       | 3 月移动平均环比 > 50%          | 环比增长率    |
| arxiv    | 周论文数 > 历史均值 2σ          | z 偏离       |
| github/community | 周频次 > 历史均值 2σ    | z 偏离       |
| course   | 周课程数 > 历史均值 2σ          | z 偏离       |

纯函数核心（判定/聚合）与 DB 读取分离，便于单元测试。
"""

import statistics
from datetime import datetime
from typing import Protocol

from app.services.extraction.dictionary import is_noise_skill


# ---- 阈值常量（设计文档 §7.2.5 条件监测矩阵）----
JD_MOM_THRESHOLD = 0.5   # JD 3 月移动平均环比 > 50%
Z_SIGMA = 2.0            # 学术/社区/课程源 2σ
HISTORY_WEEKS = 8        # 历史均值窗口（周）
JD_MA_MONTHS = 3         # JD 移动平均窗口（月）

# JD 招聘平台源（raw.source 存平台名而非字面量 "jd"，见 crawlers/spiders）
JD_SOURCES = {
    "boss", "zhilian", "maimai", "glassdoor",
    "indeed", "monster", "linkedin_public",
}


def is_jd_source(source: str) -> bool:
    """source 是否为 JD 招聘平台源。

    兼容历史字面量 "jd"（早期版本直接落库的记录）。
    """
    return source in JD_SOURCES or source == "jd"


class RawRowLike(Protocol):
    """raw 行最小接口（测试桩可替代）。"""

    source: str
    snapshot: dict
    crawled_at: str  # ISO8601


# ============================================================
# 信号提取（snapshot → 技能名集合）
# ============================================================

def _week_key(iso: str) -> str:
    """ISO8601 → 周键 YYYY-Www（不足/非法格式回退日期前 10 位）。"""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        year, week, _ = dt.isocalendar()
        return f"{year}-W{week:02d}"
    except Exception:
        return iso[:10]  # 退化：按日聚合


def extract_skills(source: str, snapshot: dict) -> list[str]:
    """从 raw 行 snapshot 提取技能名集合（按源结构不同）。"""
    snap = snapshot or {}
    if source in ("icourse163", "coursera", "edx"):
        # CourseItem.skills：技能标签列表
        return [s for s in (snap.get("skills") or []) if isinstance(s, str) and s]
    if source in ("arxiv", "github", "stackoverflow"):
        # paper 用 categories；github/so 用 language + tags
        out: list[str] = []
        cats = snap.get("categories") or []
        if isinstance(cats, list):
            out.extend(c for c in cats if isinstance(c, str) and c)
        lang = snap.get("language")
        if isinstance(lang, str) and lang:
            out.append(lang)
        tags = snap.get("tags") or []
        if isinstance(tags, list):
            out.extend(t for t in tags if isinstance(t, str) and t)
        return out
    # jd 源：LLM 抽取结果 skills（batch_extract 落 snapshot.extraction.skills）。
    # LLM 误抽的岗位名碎片/经验描述（如"算法工程师""熟悉Redis"）在 STOPWORDS
    # 之外可进 skills，入信号前用 is_noise_skill 剔除（白名单词整体保护）。
    ext = snap.get("extraction") or {}
    skills = ext.get("skills") or []
    return [
        s["name"] for s in skills
        if isinstance(s, dict) and s.get("name") and not is_noise_skill(s["name"])
    ]


# ============================================================
# 信号判定（纯函数）
# ============================================================

def aggregate_weekly_freqs(
    rows: list[RawRowLike],
) -> dict[tuple[str, str], dict[str, int]]:
    """按 ((技能, 源) → 周 → 频次) 聚合 raw 行。

    Returns:
        {(skill_name, source): {week_key: count}}
    """
    freqs: dict[tuple[str, str], dict[str, int]] = {}
    for row in rows:
        for skill in extract_skills(row.source, row.snapshot):
            wk = _week_key(row.crawled_at)
            bucket = freqs.setdefault((skill, row.source), {})
            bucket[wk] = bucket.get(wk, 0) + 1
    return freqs


def _z_score(series: list[float]) -> float | None:
    """最近一周 vs 历史窗口（不含最近周）的 z 偏离。

    历史 σ 为 0 时：历史均值为 0 返回 None（无历史基线，不判定）；
    历史全相同且非零时，完全平稳（recent == mean）返回 0.0，突变更大
    返回 Z_SIGMA / 更小返回 -Z_SIGMA（无法计算有限 z，按超阈值处理）。
    历史少于 2 周（总序列 < 3 周）无足够基线，不判定。
    """
    if len(series) < 3:
        return None
    recent = series[-1]
    history = series[:-1]
    mean = statistics.mean(history)
    if len(history) >= 2:
        stdev = statistics.pstdev(history)
    else:
        stdev = 0.0
    if stdev == 0:
        if mean == 0:
            return None
        if recent == mean:
            return 0.0
        return Z_SIGMA if recent > mean else -Z_SIGMA
    return (recent - mean) / stdev


def detect_z_signal(weekly: dict[str, int]) -> tuple[float, bool] | None:
    """周序列 → (z 偏离, 是否命中 2σ)。不足 3 周（无 2 周历史基线）返回 None。

    用 z >= Z_SIGMA 而非 >：历史 σ=0 且突变时 z 取 Z_SIGMA（阈值本身），
    > 会把该退化分支判为未命中（死分支）。
    """
    series = [float(weekly[k]) for k in sorted(weekly)]
    z = _z_score(series)
    if z is None:
        return None
    return z, z >= Z_SIGMA


def _moving_avg(series: list[float], window: int) -> list[float]:
    """窗口移动平均序列（长度 = len(series) - window + 1）。"""
    if len(series) < window:
        return []
    return [sum(series[i:i + window]) / window for i in range(len(series) - window + 1)]


def detect_jd_mom_signal(weekly: dict[str, int], window: int = JD_MA_MONTHS) -> tuple[float, bool] | None:
    """JD 周序列 → 3 月移动平均环比增长率。

    周序列按 4 周=1 月折算窗口（4×window 周）；移动平均环比 =
    (ma[-1] - ma[-2]) / ma[-2]。ma[-2]=0（无前值）时不判定（None）。
    数据不足（< 2 个移动平均点）返回 None。
    """
    series = [float(weekly[k]) for k in sorted(weekly)]
    window_weeks = 4 * window
    if len(series) < window_weeks + 1:
        return None
    ma = _moving_avg(series, window_weeks)
    if len(ma) < 2 or ma[-2] <= 0:
        return None
    growth = (ma[-1] - ma[-2]) / ma[-2]
    return growth, growth > JD_MOM_THRESHOLD


# ============================================================
# 信号汇总（DB 读取 → 命中信号）
# ============================================================

class WatchSignal:
    """命中阈值的观察池信号。"""

    __slots__ = ("skill_name", "signal_source", "signal_value", "period")

    def __init__(self, skill_name: str, signal_source: str, signal_value: float, period: str):
        self.skill_name = skill_name
        self.signal_source = signal_source
        self.signal_value = signal_value
        self.period = period


def build_signals(
    freqs: dict[tuple[str, str], dict[str, int]],
    period: str,
) -> list[WatchSignal]:
    """对聚合频次按源判定阈值，返回命中信号列表。

    jd 源走 3 月移动平均环比（>50%）；其余源（arxiv/github/stackoverflow/
    course）走周频次 2σ。同一技能可命中多个源，各产生一条信号
    （观察池按 技能×源 粒度 upsert，见 technology_watch 唯一约束）。
    """
    hits: list[WatchSignal] = []
    for (skill, source), weekly in freqs.items():
        if is_jd_source(source):
            sig = detect_jd_mom_signal(weekly)
            if sig is not None and sig[1]:
                # JD 源信号统一记 "jd"（原始 source 是平台名，如 boss/zhilian）
                hits.append(WatchSignal(skill, "jd", sig[0], period))
            continue
        sig = detect_z_signal(weekly)
        if sig is not None and sig[1]:
            hits.append(WatchSignal(skill, source, sig[0], period))
    return hits


def promotable_skills(signals: list[WatchSignal], previously_watched: set[str]) -> list[str]:
    """观察池提升判定（设计 §7.2.5 / P1 修复方案 §2）。

    提升条件：本期 JD 源信号命中阈值（3 月移动平均环比 > 50%）且该技能此前
    已在观察池（有更早周期记录）。JD 首次命中但无观察历史的技能只入池不提升，
    避免低频噪音 JD 误触发 candidate。
    """
    seen: set[str] = set()
    out: list[str] = []
    for sig in signals:
        if sig.signal_source == "jd" and sig.skill_name in previously_watched:
            if sig.skill_name not in seen:
                seen.add(sig.skill_name)
                out.append(sig.skill_name)
    return out


def promotion_features(
    freqs: dict[tuple[str, str], dict[str, int]],
    skill_name: str,
) -> dict:
    """计算观察池提升候选的置信度输入特征（P3）。

    从 (skill, JD 平台源) 周频次聚合真实特征，替代 watch_signal_daily 中
    硬编码的 source_diversity=1 / final_confidence=0.0（否则提升候选
    永远无法过 emerging 门槛：跨 ≥2 源 + 置信度 ≥ 0.6）：

    - source_diversity: 命中的 JD 平台源数
    - jd_freq_ma3: 最近 3 周 JD 频次均值（compute_confidence 的 jd_count 输入）
    - growth: 最近周环比增长率
    - z_score: JD 周频次相对历史的 z 偏离（无历史时 None）

    非 JD 源（arxiv/github 等）不计入；技能无 JD 频次时返回空 dict。
    """
    combined: dict[str, int] = {}
    sources: set[str] = set()
    for (skill, source), weekly in freqs.items():
        if skill != skill_name or not is_jd_source(source):
            continue
        sources.add(source)
        for week, count in weekly.items():
            combined[week] = combined.get(week, 0) + count

    if not combined:
        return {}

    series = [float(combined[k]) for k in sorted(combined)]
    recent3 = series[-3:]
    jd_freq_ma3 = sum(recent3) / len(recent3)
    growth = 0.0
    if len(series) >= 2 and series[-2] > 0:
        growth = (series[-1] - series[-2]) / series[-2]
    z = detect_z_signal(combined)
    return {
        "source_diversity": len(sources),
        "jd_freq_ma3": jd_freq_ma3,
        "growth": growth,
        "z_score": z[0] if z is not None else None,
    }


def anomaly_flags(
    freqs: dict[tuple[str, str], dict[str, int]],
    skill_names: set[str],
) -> dict[str, bool]:
    """技能集在学术/社区源上的 2σ 异常标记（设计 §7.2.2 辅助加分特征）。

    对每个技能查 (skill, arxiv)/(skill, github) 周频次 z 偏离是否命中 2σ，
    任一技能命中即标记该源异常。作为 candidate→emerging 置信度加分输入
    （arxiv_anomaly/github_anomaly），不参与 candidate 触发门控。
    """
    flags = {"arxiv": False, "github": False}
    for skill in skill_names:
        for source in flags:
            weekly = freqs.get((skill, source), {})
            sig = detect_z_signal(weekly)
            if sig is not None and sig[1]:
                flags[source] = True
    return flags
