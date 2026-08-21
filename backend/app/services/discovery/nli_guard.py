"""NLI 跨文档矛盾检测（RAG 接地幻觉防控，P0 软门控）。

场景：grounding.py 中 LLM 生成定义草案（hypothesis）应"忠实翻译并凝练"
权威库检索结果（premise），但转写可能引入与基座相悖的内容（事实性幻觉）。
本模块对 (premise, hypothesis) 做三分类蕴含判定：
- entailment：草案与参考基座高度重合（同语言翻译/凝练）→ 放行
- neutral：内容不完全重合但无对立 → 放行（RAG 是辅助确认，非硬门控）
- contradiction：草案与基座发生蕴含冲突 → 触发截断/重采样（软门控）

实现为无外部模型的轻量启发式 NLI（中英跨语言可用）：
① 否定极性不对称：同一主题一方肯定另一方否定（"需要 Python" vs "无需 Python"）
② 学历量级冲突：参考要求"本科以上"而草案称"大专即可"等反向量级
③ 否定断言无基座支撑：草案含强否定断言且与参考基座几乎无重合 →
   疑似脱离基座的幻觉演绎（跨语言翻译下重合度天然低，仅作重采样触发信号）

各信号独立可观测，signals 字段保留命中明细供审计与消融实验。
"""

from dataclasses import dataclass, field

# 强否定断言标记（中英，多字短语为主，避免"不"误命中"不限于/不得不"等惯用语）
_NEG_MARKERS_ZH = (
    "不需要", "无需", "无须", "不用", "不要求", "不具备", "不必",
    "缺乏", "没有", "未要求", "未涉及", "无经验", "不涉及", "不包含", "不适用",
)
_NEG_MARKERS_EN = (
    "not required", "not needed", "no need", "without", "does not require",
    "do not require", "never", "cannot", "lacks", "lacking", "absence of",
    "unnecessary", "not", "no",
)

# 学历层级（中英），用于量级冲突判定（值越高要求越高）
_DEGREE_LEVEL = {
    # 中文
    "博士": 5, "博士后": 5, "硕士": 4, "研究生": 4,
    "本科": 3, "学士": 3, "本科及以上": 3,
    "大专": 2, "专科": 2, "高职": 2,
    "高中": 1, "中专": 1,
    # 英文
    "phd": 5, "doctorate": 5, "doctoral": 5,
    "master": 4, "master's": 4, "master’s": 4,
    "bachelor": 3, "bachelor's": 3, "bachelor’s": 3, "undergraduate": 3,
    "associate": 2, "associate's": 2, "associate’s": 2,
    "high school": 1, "secondary": 1,
}

# 矛盾判定阈值：≥ CONFIRMED 截断回退；≥ SUSPICIOUS 触发一次重采样复核
CONFIRMED_THRESHOLD = 0.75
SUSPICIOUS_THRESHOLD = 0.5


@dataclass
class ContradictionResult:
    """NLI 判定结果。

    label: entailment / neutral / contradiction
    score: 矛盾置信度（0-1），≥ CONFIRMED_THRESHOLD 视为确认矛盾
    signals: 命中的矛盾信号明细（审计/消融实验用）
    """

    label: str
    score: float
    signals: list[str] = field(default_factory=list)


def _bigrams(text: str) -> set[str]:
    """字符 2-gram（小写）：中英混合文本的轻量词汇覆盖度量。"""
    t = (text or "").lower().replace(" ", "")
    return {t[i : i + 2] for i in range(max(0, len(t) - 1))}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _has_negation(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in _NEG_MARKERS_ZH) or any(
        f" {m} " in f" {low} " for m in _NEG_MARKERS_EN
    )


def _degree_level(text: str) -> int | None:
    """文本中的最高学历层级；未提及学历返回 None。"""
    low = (text or "").lower()
    best: int | None = None
    for word, lv in _DEGREE_LEVEL.items():
        if word in low and (best is None or lv > best):
            best = lv
    return best


def detect_contradiction(premise: str, hypothesis: str) -> ContradictionResult:
    """三分类蕴含判定：premise=参考基座（图谱/权威库检索结果），
    hypothesis=LLM 生成的解析文本。

    Returns:
        ContradictionResult：矛盾判定 + 置信分 + 命中信号。
    """
    if not premise or not hypothesis:
        return ContradictionResult(label="neutral", score=0.0, signals=[])

    ov = _jaccard(_bigrams(premise), _bigrams(hypothesis))
    hyp_neg = _has_negation(hypothesis)
    pre_neg = _has_negation(premise)
    signals: list[str] = []
    score = 0.0

    # ① 否定极性不对称：同语言主题重合（覆盖 ≥0.12）且极性翻转 → 确认矛盾。
    #    跨语言（英文 premise / 中文 hypothesis）重合度天然近 0，不会触发，
    #    避免"翻译即零重合"导致的误判。
    polarity_flip = hyp_neg and not pre_neg
    if polarity_flip and ov >= 0.12:
        signals.append(f"negation_asymmetry(overlap={ov:.2f})")
        score = max(score, 0.9)
    elif pre_neg and not hyp_neg and ov >= 0.12:
        signals.append(f"negation_asymmetry_reverse(overlap={ov:.2f})")
        score = max(score, 0.8)

    # ② 学历量级冲突（中英跨语言可判）：参考明确要求学历而草案给出
    #    显著更低层级（≥2 级差），如"本科"→"高中"。
    p_deg = _degree_level(premise)
    h_deg = _degree_level(hypothesis)
    if p_deg and h_deg and h_deg + 2 <= p_deg:
        signals.append(f"degree_level_conflict(premise={p_deg},hypothesis={h_deg})")
        score = max(score, 0.8)

    # ③ 否定断言无基座支撑：草案含强否定且与基座覆盖不足（含跨语言）→
    #    疑似脱离基座的幻觉演绎，触发一次重采样复核（非确认级，不直接截断）。
    if hyp_neg and ov < 0.35:
        signals.append(f"negation_assertion_without_grounding(overlap={ov:.2f})")
        score = max(score, 0.55)

    # 三分类：高分确认矛盾；高重合且无矛盾信号 → 蕴含；其余中性
    if score >= CONFIRMED_THRESHOLD:
        label = "contradiction"
    elif ov >= 0.35 and score < SUSPICIOUS_THRESHOLD:
        label = "entailment"
    else:
        label = "neutral"

    return ContradictionResult(
        label=label, score=round(score, 3), signals=signals
    )
