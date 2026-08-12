"""关键词基线评估（无 LLM）。

验证黄金集质量下限：纯关键词匹配（技能白名单扫描）应达到 F1 ≈ 0.75。
若 baseline 低于 0.60，说明黄金集标注质量或白名单覆盖有问题。

黄金集路径：data/golden_set/jd_golden_100.jsonl（100 条，gold_* 标注）。
"""

import json
import re
import sys
from pathlib import Path

# 后端根目录（tests/evaluate/ → backend/）
_BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND_DIR))

from app.services.extraction.dictionary import SKILL_WHITELIST, SKILL_ALIAS
from app.services.extraction.post_processor import canonical_skill_name


def _norm_skill(s: str) -> str:
    """评测口径规范化：与抽取管线同规则（别名归一 → 后缀清洗 → 小写）。

    黄金集标注常带后缀（如「大模型算法」）而 LLM 输出为别名标准名（「大语言模型」），
    仅 normalize_skill 无法对齐，需经 canonical_skill_name 先别名归一再后缀清洗，
    避免误判漏抽/误抽。顺序与生产链路一致（canonical_skill_name 内部先 normalize
    后 clean，修复原 normalize_skill(clean_skill_name()) 反序口径）。
    """
    return canonical_skill_name(s).lower()

_GOLDEN_PATH = _BACKEND_DIR / "data" / "golden_set" / "jd_golden_100.jsonl"


def load_golden_set(path: str) -> list[dict]:
    """加载 JSONL 格式黄金集。"""
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def _contains(text_lower: str, word: str) -> bool:
    """词典命中判定（JD 基线词边界修复，2026-08-12）。

    英文/数字词（含 +/#/. 等）用非字母数字前后断言做整词匹配——修复
    白名单短词（c/go/ai/sql/js 等）与长词（go→docker、c→cloud）的子串误报
    （此前 'c' 命中任意含 c 的英文词，'go' 命中 'docker'/'golang' 等，误报
    130+ 次占基线误报大头）；中文/混合词保持子串匹配（无词边界概念）。
    """
    w = word.lower()
    if re.fullmatch(r"[a-z0-9+#.\-]+", w):
        return re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", text_lower) is not None
    return w in text_lower


def rule_predict(text: str) -> list[str]:
    """词典关键词扫描（无 LLM 基线预测）。

    扫描白名单标准名 + SKILL_ALIAS 口语变体（大小写不敏感），
    命中后统一归一化为标准名，覆盖 JD 中 "Spring"/"Vue"/"Spark" 等口语写法。
    英文词整词匹配（_contains），中文子串匹配。
    """
    tl = text.lower()
    hits = set()
    for s in SKILL_WHITELIST:
        if _contains(tl, s):
            hits.add(s)
    for alias, std in SKILL_ALIAS.items():
        if _contains(tl, alias):
            hits.add(std)
    return sorted(hits)


# 固有噪音键上限：规范化后长度 ≤ 2 的键（单字母/短别名，如 AI/C/R/Go 及 es/JS/TS）
# 由 [EMAIL] 等脱敏占位符或英文单词子串触发，非真实技能标注。与生成脚本
# build_resume_golden_set.py 的 _NOISE_KEY_MAX_LEN 自检口径保持一致。
_NOISE_KEY_MAX_LEN = 2


def keyword_match(
    pred_skills: list[str],
    gold_skills: list[str],
    *,
    exclude_noise: bool = False,
) -> tuple[int, int, int]:
    """关键词匹配（管线同规则规范化后精确匹配），返回 (true_positive, false_positive, false_negative)。

    exclude_noise=True 时对称过滤规范化后长度 ≤ 2 的固有噪音键：
    简历黄金集已规避短技能（gold 无短键），pred 侧仅残留占位符/子串触发的单字母白名单词，
    过滤后与自检口径一致，避免评测误报。JD 黄金集标注含真实短技能（Go/AI/C 等），保持不过滤。
    """
    pred_set = {_norm_skill(s) for s in pred_skills}
    gold_set = {_norm_skill(s) for s in gold_skills}
    if exclude_noise:
        pred_set = {k for k in pred_set if len(k) > _NOISE_KEY_MAX_LEN}
        gold_set = {k for k in gold_set if len(k) > _NOISE_KEY_MAX_LEN}
    tp = len(pred_set & gold_set)
    fp = len(pred_set - gold_set)
    fn = len(gold_set - pred_set)
    return tp, fp, fn


def compute_f1(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    """计算 precision / recall / F1。"""
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return precision, recall, f1


def evaluate_jd(golden_set: list[dict]) -> dict:
    """JD 解析基线评估：白名单规则扫描 raw_text 预测 vs gold_skills 标注。"""
    total_tp, total_fp, total_fn = 0, 0, 0
    skipped = 0
    for item in golden_set:
        text = item.get("raw_text") or ""
        gold = item.get("gold_skills") or []
        if not text or not gold:
            skipped += 1
            continue
        pred = rule_predict(text)
        tp, fp, fn = keyword_match(pred, gold)
        total_tp += tp
        total_fp += fp
        total_fn += fn
    precision, recall, f1 = compute_f1(total_tp, total_fp, total_fn)
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "samples": len(golden_set) - skipped,
        "whitelist_size": len(SKILL_WHITELIST),
    }


def main():
    golden_path = _GOLDEN_PATH
    if not golden_path.exists():
        print(f"[SKIP] 黄金集不存在: {golden_path}")
        return

    golden_set = load_golden_set(str(golden_path))
    results = evaluate_jd(golden_set)

    print("=" * 50)
    print("JD 解析基线评估（白名单关键词匹配，无 LLM）")
    print("=" * 50)
    print(f"黄金集: {golden_path.relative_to(_BACKEND_DIR)}")
    print(f"样本数: {results['samples']} / {len(golden_set)}")
    print(f"白名单技能数: {results['whitelist_size']}")
    print(f"Precision: {results['precision']:.4f}")
    print(f"Recall:    {results['recall']:.4f}")
    print(f"F1:        {results['f1']:.4f}")
    print()

    if results["f1"] < 0.60:
        print("[WARN] 基线 F1 < 0.60，黄金集标注质量或白名单覆盖可能有问题")
    elif results["f1"] > 0.90:
        print("[INFO] 基线 F1 > 0.90，黄金集可能过于简单")
    else:
        print("[OK] 基线 F1 在合理区间 [0.60, 0.90]")


if __name__ == "__main__":
    main()
