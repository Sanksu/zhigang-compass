"""准确率评测统一入口（AL-M4-04，设计文档 §13.3）。

统一评测命令：`python scripts/evaluate.py --task all`，输出 reports/eval_{date}.json + reports/eval_{date}.html。

当前覆盖：
- jd      JD 解析：白名单关键词基线，字段级 F1（黄金集 data/golden_set/jd_golden_100.jsonl）
- jd_llm  JD 解析：真实 LLM 盲审评测（读取 tests/evaluate/run_manual_jd_eval.py --run 的
          最近归档 reports/eval_jd_llm_*.json；只读不重跑，避免重复消耗 LLM 额度）
- match   人岗匹配：total_score 与人工标注的 Spearman 秩相关 + 分类准确率 + Top-3 推荐准确率
          （黄金集 data/golden_set/golden_set_match.jsonl，权重来自 configs/match_weights.json）
- resume  简历提取：真实抽取（LLM + 规则兜底）vs 简历黄金集 F1
          （黄金集 data/golden_set/golden_set_resume.jsonl；未交付时跳过并注明）

除 jd_llm（读归档）外均离线可复现；LLM 在线评测（盲审）由
`tests/evaluate/run_manual_jd_eval.py --run` 单独执行并归档。缺失项跳过并注明，不伪造结果。

报告输出（设计文档 §13.3）：JSON（机器可读）+ HTML（含分项得分+错误分析+混淆矩阵）。

用法：
    uv run python scripts/evaluate.py --task all        # 全部（缺黄金集项自动跳过）
    uv run python scripts/evaluate.py --task jd
    uv run python scripts/evaluate.py --task jd_llm     # 读最近 LLM 盲审归档
    uv run python scripts/evaluate.py --task resume
    uv run python scripts/evaluate.py --task match --semantic   # 匹配项注入 SBERT 语义增强
"""

import argparse
import html as _html
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("evaluate")

from scripts.tune_match_weights import evaluate_pairs, load_pairs  # noqa: E402
from tests.evaluate.run_baseline import (  # noqa: E402
    _norm_skill,
    keyword_match,
    load_golden_set,
    rule_predict,
)

# 目标阈值（设计文档 §13.3 / §9.6）：≥ 90%
_JD_TARGET_F1 = 0.90
_RESUME_TARGET_F1 = 0.90
_MATCH_TARGET = 0.90

_JD_GOLDEN = _BACKEND_DIR / "data" / "golden_set" / "jd_golden_100.jsonl"
_RESUME_GOLDEN = _BACKEND_DIR / "data" / "golden_set" / "golden_set_resume.jsonl"
_MATCH_GOLDEN = _BACKEND_DIR / "data" / "golden_set" / "golden_set_match.jsonl"
_REPORT_DIR = _BACKEND_DIR / "reports"


def _now() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d_%H%M")


def _f1(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    """precision / recall / F1（与 tests/evaluate/run_baseline 同口径）。"""
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return precision, recall, f1


def eval_jd() -> dict:
    """JD 解析评测：白名单关键词基线（离线确定性）+ 错误分析 + 混淆矩阵。"""
    if not _JD_GOLDEN.exists():
        return {"task": "jd", "skipped": True, "reason": f"黄金集缺失: {_JD_GOLDEN.relative_to(_BACKEND_DIR)}"}

    golden = load_golden_set(str(_JD_GOLDEN))
    total_tp, total_fp, total_fn = 0, 0, 0
    error_cases: list[dict] = []
    samples = 0
    for item in golden:
        text = item.get("raw_text") or ""
        gold = item.get("gold_skills") or []
        if not text or not gold:
            continue
        samples += 1
        pred = rule_predict(text)
        tp, fp, fn = keyword_match(pred, gold)
        total_tp += tp
        total_fp += fp
        total_fn += fn
        if (fp > 0 or fn > 0) and len(error_cases) < 5:
            pred_set = {_norm_skill(s) for s in pred}
            gold_set = {_norm_skill(s) for s in gold}
            error_cases.append({
                "source_id": item.get("source_id", ""),
                "false_positives": sorted(pred_set - gold_set)[:5],
                "false_negatives": sorted(gold_set - pred_set)[:5],
            })

    precision, recall, f1 = _f1(total_tp, total_fp, total_fn)
    return {
        "task": "jd",
        "skipped": False,
        "method": "关键词基线（无 LLM，离线）",
        "samples": samples,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "target_f1": _JD_TARGET_F1,
        "target_met": f1 >= _JD_TARGET_F1,
        "confusion": {"tp": total_tp, "fp": total_fp, "fn": total_fn},
        "error_cases": error_cases,
    }


def eval_jd_llm() -> dict:
    """JD 解析评测（LLM 盲审归档）：读取最近一次 run_manual_jd_eval.py --run 的归档。

    不重跑真实 LLM（保持离线可复现 + 不重复消耗 LLM 额度）；指标口径与盲审脚本
    一致：仅 real_llm_success 样本计入。无归档/归档损坏时跳过并注明生成方式。
    """
    archives = sorted(_REPORT_DIR.glob("eval_jd_llm_*.json"))
    if not archives:
        return {
            "task": "jd_llm",
            "skipped": True,
            "reason": "无 LLM 盲审归档。先执行: uv run python tests/evaluate/run_manual_jd_eval.py --run",
        }
    latest = archives[-1]
    try:
        report = json.loads(latest.read_text(encoding="utf-8"))
        r = report["results"][0]
        # 校验归档结构完整性（防损坏归档误报）
        for key in ("task", "method", "samples", "precision", "recall", "f1", "target_f1", "target_met"):
            assert key in r, f"归档缺少字段: {key}"
    except (json.JSONDecodeError, KeyError, IndexError, AssertionError) as exc:
        return {"task": "jd_llm", "skipped": True, "reason": f"归档损坏: {latest.name}（{exc}）"}
    r = dict(r)
    r["skipped"] = False
    r["archive"] = latest.name
    return r


def eval_resume() -> dict:
    """简历提取评测：真实抽取（LLM + 规则兜底）vs 简历黄金集字段级 F1。

    黄金集每行为 {raw_text, gold_skills, ...}（与 JD 黄金集同构）。
    未交付时跳过（M5 补齐，见设计文档 §13.3 简历提取 ≥ 90%）。
    """
    if not _RESUME_GOLDEN.exists():
        return {"task": "resume", "skipped": True, "reason": f"黄金集缺失: {_RESUME_GOLDEN.relative_to(_BACKEND_DIR)}"}
    from app.services.resume.extractor import ResumeExtractor

    extractor = ResumeExtractor()
    total_tp, total_fp, total_fn, skipped, errors = 0, 0, 0, 0, 0
    for item in load_golden_set(str(_RESUME_GOLDEN)):
        text = item.get("raw_text") or ""
        gold = item.get("gold_skills") or []
        if not text or not gold:
            skipped += 1
            continue
        try:
            pred = [s.name for s in extractor.extract(text).skills]
        except Exception:
            errors += 1
            continue
        # exclude_noise：简历黄金集规避短技能，pred 侧残留占位符/子串触发的单字母噪音
        # （AI/C/R 等），对称过滤与生成脚本自检口径一致，避免评测误报
        tp, fp, fn = keyword_match(pred, gold, exclude_noise=True)
        total_tp += tp
        total_fp += fp
        total_fn += fn

    if (total_tp + total_fp + total_fn) == 0:
        return {"task": "resume", "skipped": True, "reason": "黄金集无可评测样本"}
    precision, recall, f1 = _f1(total_tp, total_fp, total_fn)
    return {
        "task": "resume",
        "skipped": False,
        "method": "真实抽取（LLM + 规则兜底）",
        "samples": len(load_golden_set(str(_RESUME_GOLDEN))) - skipped - errors,
        "skipped_samples": skipped,
        "errors": errors,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "target_f1": _RESUME_TARGET_F1,
        "target_met": f1 >= _RESUME_TARGET_F1,
        "confusion": {"tp": total_tp, "fp": total_fp, "fn": total_fn},
    }


def _top3_accuracy(pairs: list[dict], scores: list[float]) -> tuple[float | None, int]:
    """Top-3 推荐准确率（设计文档 §9.6/§13.3）。

    按候选人分组，对配对数 >= 3 的候选人，按 score 降序取 Top-3，
    看该候选人的 label=1 岗位是否在 Top-3 中。
    配对数 < 3 的候选人跳过（无法有意义计算 Top-3）。

    Returns:
        (accuracy, eligible_candidates) — accuracy 为 None 时表示无合格候选人
    """
    by_cand: dict[tuple, list[tuple[float, int]]] = defaultdict(list)
    for p, s in zip(pairs, scores):
        cand_key = tuple(p["candidate_skills"])
        by_cand[cand_key].append((s, p["label"]))

    hits, total = 0, 0
    for items in by_cand.values():
        if len(items) < 3:
            continue
        has_positive = any(l == 1 for _, l in items)
        if not has_positive:
            continue
        total += 1
        items.sort(key=lambda x: x[0], reverse=True)
        if any(l == 1 for _, l in items[:3]):
            hits += 1
    return (hits / total if total > 0 else None), total


def eval_match(semantic: bool) -> dict:
    """人岗匹配评测：Spearman 秩相关 + 分类准确率 + Top-3 推荐准确率 + 混淆矩阵。"""
    if not _MATCH_GOLDEN.exists():
        return {"task": "match", "skipped": True, "reason": f"黄金集缺失: {_MATCH_GOLDEN.relative_to(_BACKEND_DIR)}"}
    from app.services.matching.weights import load_sim_threshold, load_weights

    weights = load_weights()
    threshold = load_sim_threshold()
    sem = None
    method = "规则匹配（无语义）"
    if semantic:
        from app.services.matching.semantic import SkillEmbedder

        sem = SkillEmbedder.get()
        method = "规则 + SBERT 语义增强"
    pairs = load_pairs(_MATCH_GOLDEN)
    result = evaluate_pairs(pairs, weights, sem, threshold)
    scores = result["scores"]
    labels = result["labels"]

    # 混淆矩阵（阈值 0.5，与 evaluate_pairs 分类口径一致）
    tp = sum(1 for s, l in zip(scores, labels) if s >= 0.5 and l == 1)
    fp = sum(1 for s, l in zip(scores, labels) if s >= 0.5 and l == 0)
    tn = sum(1 for s, l in zip(scores, labels) if s < 0.5 and l == 0)
    fn = sum(1 for s, l in zip(scores, labels) if s < 0.5 and l == 1)

    # Top-3 推荐准确率（设计文档 §9.6）
    top3, top3_samples = _top3_accuracy(pairs, scores)

    # 错误样例
    error_cases: list[dict] = []
    for p, s, l in zip(pairs, scores, labels):
        is_pred_match = s >= 0.5
        is_gold_match = l == 1
        if is_pred_match != is_gold_match and len(error_cases) < 5:
            error_cases.append({
                "position_id": p.get("position_id", ""),
                "score": round(s, 4),
                "label": l,
                "error_type": "FP" if is_pred_match and not is_gold_match else "FN",
            })

    return {
        "task": "match",
        "skipped": False,
        "method": method,
        "spearman": round(result["spearman"], 4),
        "accuracy": round(result["accuracy"], 4),
        "target_accuracy": _MATCH_TARGET,
        "target_met": result["accuracy"] >= _MATCH_TARGET,
        "top3_accuracy": round(top3, 4) if top3 is not None else None,
        "top3_samples": top3_samples,
        "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "error_cases": error_cases,
    }


def _badge(met: bool) -> str:
    cls = "pass" if met else "fail"
    text = "达标" if met else "未达标"
    return f'<span class="badge {cls}">{text}</span>'


def generate_html_report(report: dict) -> str:
    """生成自包含 HTML 评测报告（设计文档 §13.3：分项得分+错误分析+混淆矩阵）。"""
    results = {r["task"]: r for r in report["results"]}

    def esc(s: str) -> str:
        return _html.escape(str(s))

    # --- 总览 ---
    overview_rows: list[str] = []
    for task_name, task_label in [
        ("jd", "JD 解析（关键词基线）"),
        ("jd_llm", "JD 解析（LLM 盲审）"),
        ("resume", "简历提取"),
        ("match", "人岗匹配"),
    ]:
        r = results.get(task_name)
        if r is None:
            continue
        if r.get("skipped"):
            overview_rows.append(
                f"<tr><td>{task_label}</td><td colspan='4'>{esc(r.get('reason', ''))}</td>"
                f"<td><span class='badge skip'>跳过</span></td></tr>"
            )
            continue
        if task_name == "match":
            metric = f"Spearman={r['spearman']:.4f}<br>Accuracy={r['accuracy']:.4f}"
            if r.get("top3_accuracy") is not None:
                metric += f"<br>Top-3={r['top3_accuracy']:.4f}"
            target = f"Acc≥{r['target_accuracy']:.2f}"
        else:
            metric = f"F1={r['f1']:.4f}"
            target = f"F1≥{r['target_f1']:.2f}"
        overview_rows.append(
            f"<tr><td>{task_label}</td><td>{esc(r.get('method', ''))}</td>"
            f"<td>{r.get('samples', '-')}</td><td>{metric}</td>"
            f"<td>{target}</td><td>{_badge(r['target_met'])}</td></tr>"
        )

    # --- JD 详情 ---
    jd_section = ""
    jd = results.get("jd")
    if jd and not jd.get("skipped"):
        c = jd.get("confusion", {})
        err_rows = "".join(
            f"<tr><td>{esc(e.get('source_id', ''))}</td>"
            f"<td>{esc(', '.join(e.get('false_positives', [])) or '—')}</td>"
            f"<td>{esc(', '.join(e.get('false_negatives', [])) or '—')}</td></tr>"
            for e in jd.get("error_cases", [])
        ) or "<tr><td colspan='3'>无错误样例</td></tr>"
        jd_section = f"""
        <div class="card">
            <h2>JD 解析评测详情</h2>
            <table>
                <tr><th>Precision</th><th>Recall</th><th>F1</th><th>样本数</th><th>目标</th><th>状态</th></tr>
                <tr><td>{jd['precision']:.4f}</td><td>{jd['recall']:.4f}</td><td>{jd['f1']:.4f}</td>
                <td>{jd['samples']}</td><td>F1≥{jd['target_f1']:.2f}</td><td>{_badge(jd['target_met'])}</td></tr>
            </table>
            <h3>混淆矩阵（多标签：TP / FP / FN）</h3>
            <table>
                <tr><th>True Positive</th><th>False Positive</th><th>False Negative</th></tr>
                <tr><td>{c.get('tp', 0)}</td><td>{c.get('fp', 0)}</td><td>{c.get('fn', 0)}</td></tr>
            </table>
            <h3>错误样例（前 5 条）</h3>
            <table>
                <tr><th>Source ID</th><th>误抽（FP）</th><th>漏抽（FN）</th></tr>
                {err_rows}
            </table>
        </div>"""

    # --- JD LLM 盲审详情（读取最近归档，不重跑） ---
    jd_llm_section = ""
    jd_llm = results.get("jd_llm")
    if jd_llm and not jd_llm.get("skipped"):
        c = jd_llm.get("confusion", {})
        bonus = jd_llm.get("bonus", {})
        err_rows = "".join(
            f"<tr><td>{esc(name)}</td><td>{count}</td></tr>"
            for name, count in jd_llm.get("error_types", [])
        ) or "<tr><td colspan='2'>无自动可分类错误</td></tr>"
        lowest_f1 = sorted(jd_llm.get("per_sample_skills_f1", []))[:3]
        lowest_html = "、".join(f"{x:.4f}" for x in lowest_f1) or "—"
        # L1-1 六维已启用：旧归档带 gap 串（缺口提示）→ 仍渲染缺口；新归档显示口径说明
        if jd_llm.get("experience_gap") or jd_llm.get("core_duties_gap"):
            gap_note = (
                f"Schema 缺口（未评测维度）：{esc(jd_llm.get('experience_gap', ''))}；"
                f"{esc(jd_llm.get('core_duties_gap', ''))}"
            )
        else:
            gap_note = "六维已启用：经验按区间重叠判定（D1-A）、核心职责按词面 containment（D2-A，L1-1 张恺天确认口径，2026-08-20）。"
        jd_llm_section = f"""
        <div class="card">
            <h2>JD 解析评测详情 · LLM 盲审（归档 {esc(jd_llm.get('archive', '?'))}）</h2>
            <table>
                <tr><th>Precision</th><th>Recall</th><th>F1（必备技能微平均）</th><th>样本数（LLM 成功）</th><th>目标</th><th>状态</th></tr>
                <tr><td>{jd_llm['precision']:.4f}</td><td>{jd_llm['recall']:.4f}</td><td>{jd_llm['f1']:.4f}</td>
                <td>{jd_llm['samples']}</td><td>F1≥{jd_llm['target_f1']:.2f}</td><td>{_badge(jd_llm['target_met'])}</td></tr>
            </table>
            <h3>分维度</h3>
            <table>
                <tr><th>岗位名（原文对齐）</th><th>岗位名（归一化后）</th><th>学历</th><th>加分技能 F1</th><th>样本平均技能 F1</th><th>经验（区间重叠）</th><th>核心职责 F1</th></tr>
                <tr><td>{jd_llm.get('title_raw_exact_accuracy', 0):.4f}</td><td>{jd_llm.get('title_normalized_accuracy', 0):.4f}</td>
                <td>{jd_llm.get('education_raw_exact_accuracy', 0):.4f}</td><td>{bonus.get('f1', 0):.4f}</td>
                <td>{jd_llm.get('skills_average_sample_f1', 0):.4f}</td><td>{jd_llm.get('experience_accuracy', 0):.4f}（n={jd_llm.get('experience_compared', 0)}）</td>
                <td>{jd_llm.get('core_duties_micro', {}).get('f1', 0):.4f}</td></tr>
            </table>
            <h3>混淆矩阵（必备技能多标签：TP / FP / FN）</h3>
            <table>
                <tr><th>True Positive</th><th>False Positive</th><th>False Negative</th></tr>
                <tr><td>{c.get('tp', 0)}</td><td>{c.get('fp', 0)}</td><td>{c.get('fn', 0)}</td></tr>
            </table>
            <h3>最低技能 F1 样本（前 3）</h3>
            <p>{lowest_html}</p>
            <h3>自动可分类错误类型</h3>
            <table>
                <tr><th>错误类型</th><th>条数</th></tr>
                {err_rows}
            </table>
            {gap_note}
        </div>"""

    # --- 简历详情 ---
    resume_section = ""
    resume = results.get("resume")
    if resume and not resume.get("skipped"):
        c = resume.get("confusion", {})
        resume_section = f"""
        <div class="card">
            <h2>简历提取评测详情</h2>
            <table>
                <tr><th>Precision</th><th>Recall</th><th>F1</th><th>样本数</th><th>目标</th><th>状态</th></tr>
                <tr><td>{resume['precision']:.4f}</td><td>{resume['recall']:.4f}</td><td>{resume['f1']:.4f}</td>
                <td>{resume['samples']}</td><td>F1≥{resume['target_f1']:.2f}</td><td>{_badge(resume['target_met'])}</td></tr>
            </table>
            <h3>混淆矩阵（多标签：TP / FP / FN）</h3>
            <table>
                <tr><th>True Positive</th><th>False Positive</th><th>False Negative</th></tr>
                <tr><td>{c.get('tp', 0)}</td><td>{c.get('fp', 0)}</td><td>{c.get('fn', 0)}</td></tr>
            </table>
        </div>"""

    # --- 匹配详情 ---
    match_section = ""
    match = results.get("match")
    if match and not match.get("skipped"):
        c = match.get("confusion", {})
        top3_str = (
            f"{match['top3_accuracy']:.4f}（{match.get('top3_samples', 0)} 个候选人）"
            if match.get("top3_accuracy") is not None
            else "N/A（无合格候选人）"
        )
        err_rows = "".join(
            f"<tr><td>{esc(e.get('position_id', ''))}</td><td>{e['score']:.4f}</td>"
            f"<td>{'匹配' if e['label'] == 1 else '不匹配'}</td><td>{e['error_type']}</td></tr>"
            for e in match.get("error_cases", [])
        ) or "<tr><td colspan='4'>无错误样例</td></tr>"
        match_section = f"""
        <div class="card">
            <h2>人岗匹配评测详情</h2>
            <table>
                <tr><th>Spearman</th><th>Accuracy</th><th>Top-3 推荐准确率</th><th>目标</th><th>状态</th></tr>
                <tr><td>{match['spearman']:.4f}</td><td>{match['accuracy']:.4f}</td><td>{top3_str}</td>
                <td>Acc≥{match['target_accuracy']:.2f}</td><td>{_badge(match['target_met'])}</td></tr>
            </table>
            <h3>混淆矩阵（二分类：阈值 0.5）</h3>
            <table>
                <tr><th></th><th>预测匹配</th><th>预测不匹配</th></tr>
                <tr><th>实际匹配</th><td>{c.get('tp', 0)} (TP)</td><td>{c.get('fn', 0)} (FN)</td></tr>
                <tr><th>实际不匹配</th><td>{c.get('fp', 0)} (FP)</td><td>{c.get('tn', 0)} (TN)</td></tr>
            </table>
            <h3>错误样例（前 5 条）</h3>
            <table>
                <tr><th>Position ID</th><th>系统评分</th><th>人工标注</th><th>错误类型</th></tr>
                {err_rows}
            </table>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>准确率评测报告 - {report['generated_at']}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
               margin: 0; padding: 40px; background: #f5f5f5; color: #1f2937; }}
        h1 {{ margin-bottom: 8px; }}
        .subtitle {{ color: #6b7280; margin-bottom: 32px; }}
        .card {{ background: white; border-radius: 8px; padding: 24px; margin-bottom: 20px;
                box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        h2 {{ margin-top: 0; border-bottom: 2px solid #e5e7eb; padding-bottom: 8px; }}
        h3 {{ color: #374151; }}
        table {{ border-collapse: collapse; width: 100%; margin-bottom: 16px; }}
        th, td {{ border: 1px solid #e5e7eb; padding: 8px 12px; text-align: left; font-size: 14px; }}
        th {{ background: #f9fafb; font-weight: 600; }}
        .badge {{ padding: 2px 8px; border-radius: 4px; font-size: 13px; font-weight: 500; }}
        .pass {{ background: #dcfce7; color: #16a34a; }}
        .fail {{ background: #fee2e2; color: #dc2626; }}
        .skip {{ background: #f3f4f6; color: #6b7280; }}
        .note {{ color: #6b7280; font-size: 13px; }}
    </style>
</head>
<body>
    <h1>智岗罗盘 · 准确率评测报告</h1>
    <p class="subtitle">生成时间：{report['generated_at']} | 目标：{esc(report['target'])}</p>
    <div class="card">
        <h2>总览</h2>
        <table>
            <tr><th>评测项</th><th>方法</th><th>样本数</th><th>核心指标</th><th>目标</th><th>状态</th></tr>
            {''.join(overview_rows)}
        </table>
    </div>
    {jd_section}
    {jd_llm_section}
    {resume_section}
    {match_section}
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="准确率评测统一入口（设计文档 §13.3）")
    parser.add_argument("--task", choices=["jd", "jd_llm", "resume", "match", "all"], default="all")
    parser.add_argument("--semantic", action="store_true", help="匹配评测注入 SBERT 语义增强")
    args = parser.parse_args()

    results = []
    if args.task in ("jd", "all"):
        results.append(eval_jd())
    if args.task in ("jd_llm", "all"):
        results.append(eval_jd_llm())
    if args.task in ("resume", "all"):
        results.append(eval_resume())
    if args.task in ("match", "all"):
        results.append(eval_match(args.semantic))

    report = {
        "generated_at": _now(),
        "target": "三项准确率 ≥ 90%（设计文档 §13.3）",
        "results": results,
    }

    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d")
    json_path = _REPORT_DIR / f"eval_{date_str}.json"
    html_path = _REPORT_DIR / f"eval_{date_str}.html"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(generate_html_report(report), encoding="utf-8")

    logger.info("=" * 56)
    logger.info("准确率评测报告（AL-M4-04）")
    logger.info("=" * 56)
    for r in results:
        if r.get("skipped"):
            logger.warning(f"[SKIP] {r['task']}: {r['reason']}")
            continue
        if r["task"] == "jd":
            logger.info(f"JD 解析   P={r['precision']:.4f} R={r['recall']:.4f} F1={r['f1']:.4f} "
                        f"({r['samples']} 条, {r['method']})")
            logger.info(f"         目标 F1≥{r['target_f1']:.2f} -> {'达标' if r['target_met'] else '未达标'}")
        elif r["task"] == "jd_llm":
            logger.info(f"JD LLM    P={r['precision']:.4f} R={r['recall']:.4f} F1={r['f1']:.4f} "
                        f"({r['samples']} 条, {r['method']})")
            logger.info(f"         标题对齐 raw={r.get('title_raw_exact_accuracy', 0):.4f} "
                        f"norm={r.get('title_normalized_accuracy', 0):.4f} 学历={r.get('education_raw_exact_accuracy', 0):.4f}")
            logger.info(f"         目标 F1≥{r['target_f1']:.2f} -> {'达标' if r['target_met'] else '未达标'} "
                        f"(归档 {r.get('archive', '?')})")
        elif r["task"] == "resume":
            logger.info(f"简历提取  P={r['precision']:.4f} R={r['recall']:.4f} F1={r['f1']:.4f} "
                        f"({r['samples']} 条, {r['method']})")
            logger.info(f"         目标 F1≥{r['target_f1']:.2f} -> {'达标' if r['target_met'] else '未达标'}")
        else:
            top3_str = f" Top-3={r['top3_accuracy']:.4f}" if r.get("top3_accuracy") is not None else ""
            logger.info(f"人岗匹配  Spearman={r['spearman']:.4f} Accuracy={r['accuracy']:.4f}{top3_str} ({r['method']})")
            logger.info(f"         目标 Acc≥{r['target_accuracy']:.2f} -> {'达标' if r['target_met'] else '未达标'}")
    logger.info(f"JSON 报告: {json_path.relative_to(_BACKEND_DIR)}")
    logger.info(f"HTML 报告: {html_path.relative_to(_BACKEND_DIR)}")


if __name__ == "__main__":
    main()
