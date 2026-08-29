"""岗位聚合（设计文档 §4.5 数据交叉验证 / §5.5 REQUIRES 边 weight 聚合）。

输入 jd_raw 已抽取记录（snapshot.extraction），计算岗位热度与技能边权重，
全量重算写回 Neo4j（幂等：重复执行仅覆盖既有值，不产生重复节点）。

聚合口径：
- Position.freq           = 命中该岗位名的 JD 条数（Evidence 数）
- Position.required_years = 该岗位 JD 经验要求最小年限的中位数（无则保留原值）
- Position.last_updated   = 该岗位最近一条 JD 的采集时间（crawled_at 规范化 ISO；
                            无 JD 时间回退本次聚合时间）——供匹配引擎时效衰减
                            （180d→0.95 / 365d→0.85）判定。此前写"本次聚合时间"
                            导致岗位恒新鲜、衰减永不触发（08-14 修复）
- Position.soft_skills    = 软技能白名单（按 JD 命中数降序，设计文档 9.2 节）
- REQUIRES.weight         = must=0.8 / nice=0.4（沿用图谱现有两档约定）
- REQUIRES.necessity      = P2-D 三重条件判 must：hit≥3 样本保护 + JD 覆盖率
                            （hit/jd_count）≥15% + must 标注占比（must_count/hit）>1/2；
                            单源/少源岗位（jd_count≤2）样本不足，继承抽取层 must 标注
                            （08-20 修复，避免必备技能全被压成加分）；
                            大岗位（jd_count≥10）hit<2 的一次性噪声边不生成
- REQUIRES.source_count   = 命中该技能的独立招聘源数

跨域降权（P2-C）：岗位族期望技能类别白名单 `_ALLOWED_SKILL_CATEGORIES`，
已分类技能不在白名单内时保留边但强制 nice（weight=0.4，不删数据）。

weight 取离散两档而非出现率连续值的原因：与匹配引擎 CII 降级（按 weight
升序降级边缘必备项）和全景图 min_weight=0.3 过滤语义兼容，且与历史手工
聚合数据格式一致。
"""

from __future__ import annotations

import re
from app.services.kg.aggregation_data import _ALLOWED_SKILL_CATEGORIES

from collections import Counter, defaultdict
from datetime import datetime
from statistics import median

from app.services.data_quality.update_status import parse_crawled_at

from app.services.extraction.dictionary import (
    SOFT_SKILL_WHITELIST,
    skill_category,
)
from app.services.extraction.post_processor import is_valid_skill_name, canonical_skill_name
from app.services.proficiency import normalize_proficiency_level

# 图谱 weight 两档约定
_WEIGHT_MUST = 0.8
_WEIGHT_NICE = 0.4
# P2-D 必要性治理（岗位评估报告 4.3 must 失衡）：
# must 判定三重条件——样本保护（hit≥3）+ JD 覆盖率（hit/jd_count≥15%）
# + must 标注占比（must_count/hit>50%）。原实现分母用 jd_count 稀释大岗位，
# 导致必须项全部判 nice；改用 hit 分母并加覆盖率门槛，恢复判别力
_MUST_THRESHOLD = 0.5
_COVERAGE_THRESHOLD = 0.15
_MIN_HIT_FOR_MUST = 3
# 单源/少源岗位兜底（08-20 修复）：jd_count ≤ 2 时岗位 JD 样本不足以做
# 跨 JD 多数表决（任何技能 hit≤jd_count≤2 <3，三重条件必然判 nice），
# 直接继承抽取层的 must 标注（must_count>0 即 must），避免必备技能全被压成加分
_SMALL_JD_THRESHOLD = 2
# P2-D 低频边过滤：jd_count≥10 的岗位，hit<2 的边视为一次性噪声不生成
# （jd_count<10 的小岗位样本不足，全量保留）
_MIN_HIT_EDGE = 2
_MIN_JD_FOR_FILTER = 10


def _is_must(sa: SkillAgg, jd_count: int) -> bool:
    """技能边是否判 must（P2-D 聚合口径 + 单源岗位兜底）。

    样本充分时（jd_count > _SMALL_JD_THRESHOLD）沿用三重条件：
    1. hit ≥ 3：样本保护，防 1-2 次出现的技能因单条 JD 标注虚高判 must
    2. hit/jd_count ≥ 15%：技能须在该岗位足够比例的 JD 中出现（普适要求）
    3. must_count/hit > 50%：出现该技能的 JD 中超半数标 must

    单源/少源岗位兜底（08-20 修复）：jd_count ≤ _SMALL_JD_THRESHOLD 时，
    岗位 JD 样本不足以做跨 JD 多数表决（任何技能 hit≤jd_count≤2 <3，
    三重条件必然判 nice），直接继承抽取层的 must 标注——该岗位任一 JD
    将技能标 must（must_count > 0）即判 must，否则 nice。
    """
    if jd_count <= 0:
        return False
    if jd_count <= _SMALL_JD_THRESHOLD:
        return sa.must_count > 0
    if sa.hit < _MIN_HIT_FOR_MUST:
        return False
    coverage = sa.hit / jd_count if jd_count else 0
    return coverage >= _COVERAGE_THRESHOLD and sa.must_count / sa.hit > _MUST_THRESHOLD


def _is_cross_domain(pos: str, skill_name: str) -> bool:
    """P2-C 跨域判定：岗位族有期望技能类别白名单时，已分类技能不在白名单内视为跨域。

    降权策略（保留边、不删数据）：跨域技能强制 nice，避免污染匹配与图谱可视化。
    未分类技能（category=未分类）不降权（白名单外待审核，不武断）。
    """
    allowed = _ALLOWED_SKILL_CATEGORIES.get(pos)
    if not allowed:
        return False
    cat = skill_category(skill_name)
    return cat != "未分类" and cat not in allowed





def _most_common_level(levels: list[object]) -> str:
    """规范熟练度的众数（并列取出现最早的一档）；无有效等级返回空串。"""
    normalized_levels = [
        normalized for level in levels
        if (normalized := normalize_proficiency_level(level)) is not None
    ]
    if not normalized_levels:
        return ""
    return Counter(normalized_levels).most_common(1)[0][0]


class SkillAgg:
    __slots__ = ("hit", "must_count", "sources", "levels")

    def __init__(self) -> None:
        self.hit = 0
        self.must_count = 0
        self.sources: set[str] = set()
        # 该技能在岗位 JD 中的熟练度 level 收集（聚合取众数写回 REQUIRES.level）
        self.levels: list[str] = []


_SALARY_RANGE_RE = re.compile(
    r"[US$￥¥]?\s*([\d,]+(?:\.\d+)?)\s*(万|千|[kK])?\s*(?:元)?\s*"
    r"(?:[-~至到–—]|to)"  # 分隔符：连字符系或英文 to（组结构不变）
    r"\s*[US$￥¥]?\s*([\d,]+(?:\.\d+)?)\s*(万|千|[kK])?\s*(?:元)?"
)
_SALARY_PERIOD_RE = re.compile(r"(?:/|每)?\s*(月|年|天|日|时|小时|hr|hour)")


def _salary_to_monthly(value: float, unit: str, period: str) -> float:
    """数值 + 单位（万/千/k/K/空）+ 周期 → 元/月。unit 为空按 1（纯元）。

    单位共享在 parse_salary_range 内完成（hi 带单位则 lo 跟随）——此处
    不做数值量级猜测：'200-300元/天' 的 200 若猜千会放大千倍（实证坑）。
    """
    mult = {"万": 10000.0, "千": 1000.0, "k": 1000.0, "K": 1000.0}.get(unit, 1.0)
    v = value * mult
    return v * {"月": 1.0, "年": 1 / 12, "天": 22.0, "日": 22.0, "时": 176.0,
                "小时": 176.0, "hr": 176.0, "hour": 176.0}.get(period, 1.0)


def parse_salary_range(text: str | None) -> tuple[float, float, str] | None:
    """解析 salary_range 文本为 (min, max, currency)；无法解析返回 None。

    实测语料形态（7056 条，覆盖 ~87%）：'8000-12000元'、'1-1.5万·13薪'、
    '15k-25k'、'10-20万/年'、'200-300元/天'、'$104,000-$150,000 annually'、
    '$60.90/hr-$82.30/hr'。周期缺省按月；年÷12、天/日×22、时/hr×176。
    千分位逗号剥除；'面议'/单值等不解析（宁缺毋滥）。

    币种（08-29 国内外区分拍板）：$/$$/US$ → USD（美元数值原样保留，
    不折算人民币——币种是一等维度，统计与展示按 currency 分组）；
    ￥/¥/元/万/千 或纯数字 → CNY。
    """
    if not text:
        return None
    m = _SALARY_RANGE_RE.search(text)
    if not m:
        return None
    currency = "USD" if re.search(r"(?:US)?\$|USD", text[: m.start() + 4], re.I) else "CNY"
    # 周期检测：范围尾部或中间的 /年 /天 /hr 小时 等（缺省按月）
    # 周期：范围尾部（/年 ·13薪）或两数之间（A/hr-B/hr）取首个命中；缺省按月
    period = ""
    tail = _SALARY_PERIOD_RE.search(text, m.end())
    if tail and tail.start() - m.end() <= 8:
        period = tail.group(1) or ""
    else:
        mid = _SALARY_PERIOD_RE.search(text[m.start():m.end()])
        if mid:
            period = mid.group(1) or ""
    # 单位推断共享：任一侧显式带单位（万/千/k），无单位侧跟随同乘
    # （'1-1.5万' lo 同乘万；'10-20万' 同理——量级推断只对双侧无单位生效）
    unit_lo, unit_hi = m.group(2) or "", m.group(4) or ""
    if not unit_lo and unit_hi:
        unit_lo = unit_hi
    elif not unit_hi and unit_lo:
        unit_hi = unit_lo
    lo = _salary_to_monthly(float(m.group(1).replace(",", "")), unit_lo, period)
    hi = _salary_to_monthly(float(m.group(3).replace(",", "")), unit_hi, period)
    if lo > hi:
        lo, hi = hi, lo
    # 异常值护栏：折算后月薪 <500 或 >500_000 视为解析噪声（币种内判断：
    # USD 护栏放大 8 倍——美元数值天然大 7 倍左右）
    floor, ceil = (500, 500_000) if currency == "CNY" else (1_000, 1_000_000)
    if hi < floor or hi > ceil:
        return None
    return (lo, hi, currency)


class PositionAgg:
    __slots__ = (
        "jd_count", "skills", "exp_years", "soft_skills", "typical_scenarios",
        "last_crawled", "education_levels", "salaries",
        "experience_distribution", "salary_text",
    )

    def __init__(self) -> None:
        self.jd_count = 0
        self.skills: dict[str, SkillAgg] = defaultdict(SkillAgg)
        self.exp_years: list[float] = []
        # 学历级别收集（聚合取众数写 Position.required_education，
        # 并按 jd 条数落 Position.education_distribution 供前端多值+证据展示）
        self.education_levels: Counter = Counter()
        # 经验标注分布（'3年以上' → jd 条数；未标注不入，画像展示诚实口径）
        self.experience_distribution: Counter = Counter()
        # 薪资原文档位计数（同币种同量级归档 → Top-3 档位 + 条数证据）
        self.salary_text: Counter = Counter()
        # 薪资月范围按币种分桶（解析 salary_range 文本 → (min, max)），
        # CNY/USD 各自取中位区间写 salary_min/max + salary_currency
        self.salaries: dict[str, list[tuple[float, float]]] = defaultdict(list)
        # 软技能白名单命中的 JD 数（写回 Position.soft_skills，设计文档 9.2 节）
        self.soft_skills: Counter = Counter()
        # 典型项目场景文本计数（写回 Position.typical_scenarios，按频次降序截断）
        self.typical_scenarios: Counter = Counter()
        # 最近一条参与聚合 JD 的采集时间（写回 Position.last_updated 供时效衰减）
        self.last_crawled: datetime | None = None


def _min_experience_years(snapshot: dict) -> float | None:
    """解析 JD 经验要求最小年限（如 "3-5年" → 3.0），无法解析返回 None。"""
    m = re.search(r"(\d+)", str(snapshot.get("experience") or ""))
    return float(m.group(1)) if m else None


def _position_skills(ext: dict) -> list[tuple[str, str, str]]:
    """岗位技能列表 (skill_name, necessity, level)。requirements 优先，skills 补充。

    技能名与抽取链路一致归一化（normalize_skill → clean_skill_name → 黑名单/单字符
    过滤）：jd_raw 快照可能早于 P1-1/P1-2 扩充抽取（存 Vue3/reactjs、嵌入式/前端等
    泛词），聚合时归一化才能命中已合并的规范节点，防止聚合把属性写回旧名节点、
    重建旧名 Skill，或把泛词频次计入聚合。

    requirements 是 skills 的子集（LLM 只把"必备/加分"项移入 requirements），
    漏掉 skills 中未进 requirements 的技能会导致其频次/来源数被低估，被 P2-D
    低频过滤线误裁——故以 requirements 为准、skills 未覆盖的以 nice 并入。
    """
    def _norm(name: str) -> str:
        n = canonical_skill_name(name)
        return n if is_valid_skill_name(n) else ""

    reqs = ext.get("requirements") or []
    out: list[tuple[str, str, str]] = []
    req_names: set[str] = set()
    for r in reqs:
        n = _norm(r.get("skill_name", ""))
        if n:
            req_names.add(n)
            out.append((n, r.get("necessity", "nice"), r.get("level") or ""))
    for s in ext.get("skills") or []:
        n = _norm(s.get("name", ""))
        if n and n not in req_names:
            out.append((n, "nice", ""))
    return out


def _jd_decay_weight(snap: dict) -> float:
    """JD 级降权系数（设计文档 §4.7/§4.8 聚合消费）。

    时滞（snapshot.validation）与通胀（snapshot.inflation）降权取更严格者，
    与 temporal_detector.apply_temporal_decay 内部"取最严重"口径一致。
    返回 0.0 表示该 JD 归档不入聚合（content_obsolete）；无检测记录返回 1.0，
    保持对未跑时滞/通胀检测的历史数据行为不变。
    """
    weights = [1.0]
    for key in ("validation", "inflation"):
        decay = (snap.get(key) or {}).get("decay_weight")
        if decay is not None:
            weights.append(float(decay))
    return min(weights)


# 设计文档 §4.8 岗位级/平台级通胀处置：
# 岗位级：岗位内通胀 JD 占比 ≥30% 时，通胀 JD 完全剔除出聚合
# （技能 hit/must_count/jd_count 均不贡献，等价 decay=0）
_POSITION_INFLATION_EXCLUSION_RATIO = 0.30
# 平台级：源内通胀 JD 占比 >50% 时，该源全部 JD 额外降权
# 系数取 ×0.5（与内容时滞 stale 降权对齐，文档未定义该值）
_SOURCE_INFLATION_THRESHOLD = 0.50
_SOURCE_INFLATION_WEIGHT = 0.5


def _is_inflated(snap: dict) -> bool:
    """JD 是否被判定为通胀（inflation.label != normal，含 mild/severe）。"""
    label = (snap.get("inflation") or {}).get("label")
    return label is not None and label != "normal"


def _inflation_stats(rows) -> tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, int]]:
    """岗位级/平台级通胀占比统计（设计文档 §4.8）。

    返回 (pos_total, pos_inflated, src_total, src_inflated) 四组计数：
    岗位/源 → JD 总数 / 通胀 JD 数。跳过 _duplicate_of 与空岗位名，
    与 build_aggregates 主循环口径一致。
    """
    from app.services.extraction.position_normalization import normalized_position_from_snapshot

    pos_total: Counter = Counter()
    pos_inflated: Counter = Counter()
    src_total: Counter = Counter()
    src_inflated: Counter = Counter()
    for row in rows:
        snap = row.snapshot or {}
        if snap.get("_duplicate_of"):
            continue
        pos = normalized_position_from_snapshot(snap)
        if not pos:
            continue
        source = row.source or ""
        pos_total[pos] += 1
        src_total[source] += 1
        if _is_inflated(snap):
            pos_inflated[pos] += 1
            src_inflated[source] += 1
    return pos_total, pos_inflated, src_total, src_inflated


def build_aggregates(rows) -> dict[str, PositionAgg]:
    """从 jd_raw 已抽取记录聚合。

    rows 需为 JDRaw ORM 行（使用 row.snapshot / row.source）。
    岗位名经 normalize_position_name 归一化（与 import_jd 入图命名一致，
    否则聚合写回 MATCH {name} 匹配不上图谱节点）；空岗位名不参与聚合。

    时滞/通胀降权（设计文档 §4.7/§4.8）：被降权 JD 的技能 hit/must_count
    按系数加权（stale ×0.5 / 僵尸 ×0.3 / 抄袭 ×0.4 / 高通胀 ×0.4），归档
    （×0）完全跳过；jd_count 仍计真实 JD 条数——Position.freq 是事实统计，
    且演化/发现链路依赖整数频次，降权作用在技能边贡献上。

    岗位级/平台级通胀（设计文档 §4.8）：
    - 岗位级：岗位内通胀 JD 占比 ≥30% 时，通胀 JD 完全剔除（与归档同级，
      jd_count 不计，避免虚高 JD 污染岗位频次与技能边）
    - 平台级：源内通胀 JD 占比 >50% 时，该源全部 JD 额外降权 ×0.5
    """
    from app.services.extraction.position_normalization import normalized_position_from_snapshot

    pos_total, pos_inflated, src_total, src_inflated = _inflation_stats(rows)
    agg: dict[str, PositionAgg] = defaultdict(PositionAgg)
    for row in rows:
        snap = row.snapshot or {}
        # SimHash 近似重复（设计文档 §4.2 消费方）：保留先入库版本，
        # 被标记 _duplicate_of 的后入库记录不参与聚合，避免重复 JD 虚高频次
        if snap.get("_duplicate_of"):
            continue
        ext = snap.get("extraction") or {}
        pos = normalized_position_from_snapshot(snap)
        if not pos:
            continue
        source = row.source or ""
        # 岗位级通胀排除（§4.8）：岗位内通胀占比 ≥30% 时，通胀 JD 完全剔除
        if (
            _is_inflated(snap)
            and pos_total[pos] > 0
            and pos_inflated[pos] / pos_total[pos] >= _POSITION_INFLATION_EXCLUSION_RATIO
        ):
            continue
        # 时滞/通胀降权消费：归档 JD 不入聚合
        jd_weight = _jd_decay_weight(snap)
        if jd_weight == 0:
            continue
        # 平台级源降权（§4.8）：源内通胀占比 >50% 时，该源全部 JD ×0.5
        if (
            src_total[source] > 0
            and src_inflated[source] / src_total[source] > _SOURCE_INFLATION_THRESHOLD
        ):
            jd_weight = min(jd_weight, _SOURCE_INFLATION_WEIGHT)
        pa = agg[pos]
        pa.jd_count += 1
        # 时效衰减依据（08-14 修复）：记录该岗位最近一条 JD 的采集时间
        # （此前 last_updated 写聚合时间，岗位恒新鲜，engine 的 180d/365d 惩罚永不触发）
        crawled = getattr(row, "crawled_at", None)
        if crawled:
            dt = parse_crawled_at(crawled)
            if dt is not None and (pa.last_crawled is None or dt > pa.last_crawled):
                pa.last_crawled = dt
        years = _min_experience_years(snap)
        if years is not None:
            pa.exp_years.append(years)
            # 经验分布（原文标注口径：仅正文明确年限的 JD 计数）
            pa.experience_distribution[f"{years:g}年以上"] += 1
        # 学历要求：抽取六维 education.level（大专/本科/硕士/博士），聚合取众数
        edu = ((ext.get("education") or {}).get("level") or "").strip()
        if edu:
            pa.education_levels[edu] += 1
        # 薪资原文档位（解析成功为前提，原文串作档位键保留币种/形态信息）
        salary_text = (ext.get("salary_range") or "").strip()
        if salary_text and parse_salary_range(salary_text):
            pa.salary_text[salary_text] += 1
        # 薪资：salary_range 文本解析为月范围数值，按币种分桶（08-29 国内外区分）——
        # CNY/USD 各自取中位区间，绝不混算
        parsed = parse_salary_range(ext.get("salary_range"))
        if parsed:
            lo, hi, cur = parsed
            pa.salaries[cur].append((lo, hi))
        source = row.source or ""
        for skill, necessity, level in _position_skills(ext):
            sa = pa.skills[skill]
            sa.hit += jd_weight
            sa.sources.add(source)
            normalized_level = normalize_proficiency_level(level)
            if normalized_level is not None:
                sa.levels.append(normalized_level)
            if necessity == "must":
                sa.must_count += jd_weight
        # 软技能：仅统计岗位本体白名单（JD 抽取已过滤，此处兜底再校验）
        for soft in ext.get("soft_skills") or []:
            soft = soft.strip()
            if soft in SOFT_SKILL_WHITELIST:
                pa.soft_skills[soft] += 1
        # 典型场景：name + description 拼合为比对文本（与候选项目文本口径一致，
        # loaders.build_candidate 的 CandidateProject 同为 "name：description"）
        for sc in ext.get("typical_scenarios") or []:
            if not isinstance(sc, dict):
                continue
            name = (sc.get("name") or "").strip()
            if not name:
                continue
            desc = (sc.get("description") or "").strip()
            pa.typical_scenarios[f"{name}：{desc}" if desc else name] += 1
    return agg


def build_edges(agg: dict[str, PositionAgg]) -> list[dict]:
    """构建写回图谱的 REQUIRES 边列表（P2 过滤+降权口径）。

    与 write_aggregates 写边逻辑完全一致，独立成纯函数供 cleanup_graph 对齐清理
    复用，避免"清理口径 ≠ 聚合写边口径"导致低频噪声边残留。
    """
    edges = []
    for pos, pa in agg.items():
        for skill, sa in pa.skills.items():
            # P2-D 低频边过滤：大岗位（jd_count≥10）hit<2 的边是一次性噪声不生成；
            # 小岗位样本不足，保留全部边避免信息缺失
            if pa.jd_count >= _MIN_JD_FOR_FILTER and sa.hit < _MIN_HIT_EDGE:
                continue
            is_must = _is_must(sa, pa.jd_count)
            # P2-C 跨域降权：跨域技能保留边但强制 nice（不删数据），
            # 避免前端技能污染后端匹配/全景图
            if is_must and _is_cross_domain(pos, skill):
                is_must = False
            edges.append({
                "pos": pos,
                "skill": skill,
                "weight": _WEIGHT_MUST if is_must else _WEIGHT_NICE,
                "necessity": "must" if is_must else "nice",
                "source_count": len(sa.sources),
                "level": _most_common_level(sa.levels),
            })
    return edges


def write_aggregates(session, agg: dict[str, PositionAgg], now: str) -> dict:
    """全量写回 Neo4j（UNWIND 批量 + MERGE 幂等 + 对齐删除）。

    返回 {"positions", "edges", "removed_edges"}：positions/edges 为写入的
    岗位/边数；removed_edges 为对齐删除的聚合输出之外 REQUIRES 边数。
    """
    positions = []
    for pos, pa in agg.items():
        # 学历众数（并列取级别高者——排序稳定靠 Counter.most_common 计数优先）
        edu_mode = pa.education_levels.most_common(1)[0][0] if pa.education_levels else None
        # 薪资按币种各自中位：CNY 优先（国内主口径），无 CNY 用 USD；
        # currency 落图供前端分组展示（绝不混算，08-29 拍板）
        salary_cur = "CNY" if pa.salaries.get("CNY") else ("USD" if pa.salaries.get("USD") else None)
        salary_min = median([s[0] for s in pa.salaries[salary_cur]]) if salary_cur else None
        salary_max = median([s[1] for s in pa.salaries[salary_cur]]) if salary_cur else None
        # 多值画像分布（08-29 证据计数展示）：按 jd 条数降序 Top-5，
        # 未标注不入图（诚实口径——抽取覆盖面在详情页以 evidence_count 呈现）
        edu_dist = {k: v for k, v in pa.education_levels.most_common(5)}
        exp_dist = {k: v for k, v in pa.experience_distribution.most_common(5)}
        salary_tiers = [
            {"text": text, "count": cnt}
            for text, cnt in pa.salary_text.most_common(5)
        ]
        positions.append({
            "pos": pos,
            "freq": pa.jd_count,
            "evidence_count": pa.jd_count,
            "req_years": median(pa.exp_years) if pa.exp_years else None,
            "req_education": edu_mode,
            "education_distribution": edu_dist,
            "experience_distribution": exp_dist,
            "salary_tiers": salary_tiers,
            "salary_min": round(salary_min) if salary_min is not None else None,
            "salary_max": round(salary_max) if salary_max is not None else None,
            "salary_currency": salary_cur,
            # 最近 JD 采集时间（规范化 ISO）；无 JD 时间（旧数据）回退聚合时间
            "last_updated": pa.last_crawled.isoformat() if pa.last_crawled else now,
            # 软技能按 JD 命中数降序（低频软技能不写入岗位本体）
            "soft_skills": [s for s, _ in pa.soft_skills.most_common()],
            # 典型场景按 JD 命中数降序，上限 20 条防属性膨胀（仅非空时 SET）
            "typical_scenarios": [s for s, _ in pa.typical_scenarios.most_common(20)],
        })
    edges = build_edges(agg)

    removed_edges = 0
    with session:
        if positions:
            session.run(
                """
                UNWIND $items AS it
                MATCH (p:Position {name: it.pos})
                SET p.freq = it.freq,
                    p.last_updated = it.last_updated,
                    p.required_years = coalesce(it.req_years, p.required_years),
                    p.required_education = coalesce(it.req_education, p.required_education),
                    p.evidence_count = it.evidence_count,
                    p.education_distribution = it.education_distribution,
                    p.experience_distribution = it.experience_distribution,
                    p.salary_tiers = it.salary_tiers,
                    p.salary_min = coalesce(it.salary_min, p.salary_min),
                    p.salary_max = coalesce(it.salary_max, p.salary_max),
                    p.salary_currency = coalesce(it.salary_currency, p.salary_currency),
                    p.soft_skills = it.soft_skills,
                    p.typical_scenarios = coalesce(
                        CASE WHEN size(it.typical_scenarios) > 0
                             THEN it.typical_scenarios ELSE null END,
                        p.typical_scenarios
                    )
                """,
                items=positions,
            )
        if edges:
            session.run(
                """
                UNWIND $edges AS e
                MATCH (p:Position {name: e.pos}), (s:Skill {name: e.skill})
                MERGE (p)-[r:REQUIRES]->(s)
                SET r.weight = e.weight,
                    r.necessity = e.necessity,
                    r.source_count = e.source_count,
                    r.level = e.level
                """,
                edges=edges,
            )
            # P1-1 衰退技能移除（设计文档 §7.1.1）：聚合为全量重算，仅 MERGE
            # 会永久保留聚合输出之外的边（SimHash 重复 JD 独有技能、大岗位
            # hit<2 一次性噪声、JD 已消失的衰退技能），按本次输出对齐删除。
            # 人工编辑岗位（PositionEditLog）跳过——人工调整优先于自动聚合，
            # 防止下次聚合把编辑结果打回。
            edited = session.run(
                "MATCH (l:PositionEditLog) "
                "RETURN collect(DISTINCT l.position_name) AS names"
            ).single()["names"]
            kept_by_pos: dict[str, list[str]] = {}
            for e in edges:
                kept_by_pos.setdefault(e["pos"], []).append(e["skill"])
            removed_edges = session.run(
                """
                UNWIND $kept_by_pos AS item
                MATCH (p:Position {name: item.pos})-[r:REQUIRES]->(s:Skill)
                WHERE NOT s.name IN item.kept AND NOT p.name IN $excluded
                DELETE r RETURN count(r) AS c
                """,
                kept_by_pos=[{"pos": p, "kept": k} for p, k in kept_by_pos.items()],
                excluded=edited,
            ).single()["c"]
    return {"positions": len(positions), "edges": len(edges),
            "removed_edges": removed_edges}
