"""Evaluate independently reviewed JD records through the current JDExtractor.

This is an evaluation-only entry point.  It never writes the source workbook or
golden-set JSONL.  A real run is deliberately blocked unless the blind-review
labels pass strict format checks; this prevents rule-based fallback or malformed
human labels from being reported as an LLM extraction baseline.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_XLSX = ROOT / "data" / "golden_set" / "review" / "jd_manual_review_round1.xlsx"
DEFAULT_OUTPUT = ROOT / "data" / "golden_set" / "review" / "evaluation"
NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}
LABEL_COLUMNS = (
    "review_gold_title",
    "review_gold_skills",
    "review_gold_bonus_skills",
    "review_gold_experience",
    "review_gold_education",
    "review_gold_core_duties",
)
ARRAY_COLUMNS = (
    "review_gold_skills",
    "review_gold_bonus_skills",
    "review_gold_core_duties",
)


def _cell_column(cell_ref: str) -> str:
    return re.match(r"[A-Z]+", cell_ref).group(0)  # type: ignore[union-attr]


def _load_round1_blind_rows(path: Path, sheet_name: str) -> list[dict[str, str]]:
    """Read the requested sheet with only stdlib so validation needs no extra package."""
    with zipfile.ZipFile(path) as book:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in book.namelist():
            root = ET.fromstring(book.read("xl/sharedStrings.xml"))
            shared = ["".join(si.itertext()) for si in root.findall("m:si", NS)]

        workbook = ET.fromstring(book.read("xl/workbook.xml"))
        rels = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
        targets = {
            rel.attrib["Id"]: rel.attrib["Target"]
            for rel in rels.findall("pr:Relationship", NS)
        }
        target = None
        for sheet in workbook.findall("m:sheets/m:sheet", NS):
            if sheet.attrib.get("name") == sheet_name:
                target = targets[sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]]
                break
        if target is None:
            raise ValueError(f"Workbook has no worksheet named {sheet_name!r}")
        sheet_path = "xl/" + target.lstrip("/")
        if not sheet_path.startswith("xl/worksheets/"):
            sheet_path = "xl/worksheets/" + Path(target).name
        sheet = ET.fromstring(book.read(sheet_path))

        rows: list[dict[str, str]] = []
        for xml_row in sheet.findall("m:sheetData/m:row", NS):
            result: dict[str, str] = {}
            for cell in xml_row.findall("m:c", NS):
                ref = cell.attrib.get("r", "")
                col = _cell_column(ref)
                kind = cell.attrib.get("t")
                formula = cell.findtext("m:f", default="", namespaces=NS)
                value = cell.findtext("m:v", default="", namespaces=NS)
                if formula:
                    # Artifact-tool writes source_url as HYPERLINK("url","url").
                    match = re.search(r'HYPERLINK\("([^"]+)"', formula)
                    result[col] = match.group(1) if match else formula
                elif kind == "s" and value:
                    result[col] = shared[int(value)]
                elif kind == "inlineStr":
                    result[col] = "".join(cell.find("m:is", NS).itertext())  # type: ignore[union-attr]
                else:
                    result[col] = value
            rows.append(result)
    if not rows:
        return []
    headers = rows[0]
    return [
        {headers.get(column, column): values.get(column, "") for column in headers}
        for values in rows[1:]
    ]


def _json_array(value: str) -> tuple[list[str] | None, str | None]:
    # A blank human field may deliberately mean that the JD makes no explicit
    # statement.  Treat it as an empty gold set rather than an unfinished label.
    if not value.strip():
        return [], None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc.msg}"
    if not isinstance(decoded, list) or not all(isinstance(item, str) for item in decoded):
        return None, "must be a JSON array of strings"
    return decoded, None


def _json_object_or_empty(value: str) -> tuple[dict[str, Any] | None, str | None]:
    if not value.strip():
        return None, None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc.msg}"
    if not isinstance(decoded, dict):
        return None, "must be empty or a JSON object"
    return decoded, None


def validate_rows(rows: list[dict[str, str]]) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    annotators = sorted({row.get("annotator", "").strip() for row in rows})
    valid_rows = 0
    for row in rows:
        sid = row.get("sample_id", "<missing sample_id>")
        valid = True
        if not row.get("detail_raw_text", "").strip():
            issues.append({"sample_id": sid, "field": "detail_raw_text", "issue": "正文为空"})
            valid = False
        if not row.get("source_url", "").strip():
            issues.append({"sample_id": sid, "field": "source_url", "issue": "不可追溯：URL 为空"})
            valid = False
        for column in ARRAY_COLUMNS:
            _, error = _json_array(row.get(column, ""))
            if error:
                issues.append({"sample_id": sid, "field": column, "issue": error})
                valid = False
        _, error = _json_object_or_empty(row.get("review_gold_experience", ""))
        if error:
            issues.append({"sample_id": sid, "field": "review_gold_experience", "issue": error})
            valid = False
        if valid:
            valid_rows += 1
    provenance_ok = len(annotators) == 1 and bool(annotators[0])
    return {
        "row_count": len(rows),
        "observed_annotators": annotators,
        "annotator_nonempty_and_consistent": provenance_ok,
        "rows_with_nonempty_detail": sum(bool(r.get("detail_raw_text", "").strip()) for r in rows),
        "rows_with_source_url": sum(bool(r.get("source_url", "").strip()) for r in rows),
        "fully_valid_rows": valid_rows,
        "issues": issues,
        "ready_for_real_run": len(rows) == 12 and valid_rows == 12 and provenance_ok,
    }


def _to_jsonable(value: Any) -> Any:
    return value.model_dump() if hasattr(value, "model_dump") else value


def _safe_exception_summary(exc: Exception) -> str:
    """Persist only an error type/status, never provider request details or keys."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and 100 <= status <= 599:
        return f"{type(exc).__name__}: HTTP {status}"
    return f"{type(exc).__name__}: provider call failed"


class TrackingLLM:
    """Delegates to the real provider chain and records whether it really returned."""

    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.model_output: Any = None
        self.error: str | None = None

    def extract_structured(self, *args: Any, **kwargs: Any) -> Any:
        try:
            self.model_output = self.delegate.extract_structured(*args, **kwargs)
            return self.model_output
        except Exception as exc:  # JDExtractor will decide whether to fall back.
            self.error = _safe_exception_summary(exc)
            raise


def _metric(tp: int, fp: int, fn: int) -> dict[str, float | int]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0}


def _compare_set(gold: list[str], predicted: list[str]) -> dict[str, Any]:
    g, p = set(gold), set(predicted)
    return {"tp": sorted(g & p), "fp": sorted(p - g), "fn": sorted(g - p), "f1": _metric(len(g & p), len(p - g), len(g - p))["f1"]}


def run_real_eval(rows: list[dict[str, str]], output_dir: Path) -> dict[str, Any]:
    """Run the current extractor and reject all records that fall back to rules."""
    sys.path.insert(0, str(ROOT))
    from app.services.extraction.dictionary import normalize_position_name, normalize_skill
    from app.services.extraction.jd_extractor import JDExtractor
    from app.services.extraction.llm_provider import LLMConfigurationError, LLMProviderChain
    from app.services.extraction.post_processor import clean_skill_name

    try:
        provider = LLMProviderChain()
    except LLMConfigurationError as exc:
        raise RuntimeError(f"LLMProviderChain 不可用：{type(exc).__name__}: {exc}") from exc

    predictions: list[dict[str, Any]] = []
    title_hits = 0
    title_raw_hits = 0
    education_hits = 0
    skills_total: Counter[str] = Counter()
    bonus_total: Counter[str] = Counter()
    sample_skill_f1: list[float] = []
    sample_bonus_f1: list[float] = []
    fallback_samples = 0
    failed_samples = 0
    for row in rows:
        tracker = TrackingLLM(provider)
        try:
            result = JDExtractor(llm=tracker).extract(row["detail_raw_text"])
        except Exception as exc:
            failed_samples += 1
            predictions.append({
                "sample_id": row["sample_id"], "source": row["source"], "source_id": row["source_id"],
                "source_url": row["source_url"], "job_title_raw": row["job_title_raw"],
                "human_gold": {key: row.get(key, "") for key in LABEL_COLUMNS},
                "execution_status": "failed",
                "failure_reason": _safe_exception_summary(exc),
            })
            continue
        if tracker.model_output is None:
            fallback_samples += 1
            predictions.append({
                "sample_id": row["sample_id"], "source": row["source"], "source_id": row["source_id"],
                "source_url": row["source_url"], "job_title_raw": row["job_title_raw"],
                "human_gold": {key: row.get(key, "") for key in LABEL_COLUMNS},
                "execution_status": "fallback",
                "fallback_reason": tracker.error or "JDExtractor returned without a tracked LLM response",
                "fallback_output_not_used_for_metrics": _to_jsonable(result),
            })
            continue
        gold_skills, _ = _json_array(row["review_gold_skills"])
        gold_bonus, _ = _json_array(row["review_gold_bonus_skills"])
        assert gold_skills is not None and gold_bonus is not None
        normalized_gold_skills = [clean_skill_name(normalize_skill(x)) for x in gold_skills]
        normalized_gold_bonus = [clean_skill_name(normalize_skill(x)) for x in gold_bonus]
        predicted_skills = [skill.name for skill in result.skills]
        predicted_bonus = [req.skill_name for req in result.requirements if req.necessity == "nice"]
        skills_cmp = _compare_set(normalized_gold_skills, predicted_skills)
        bonus_cmp = _compare_set(normalized_gold_bonus, predicted_bonus)
        sample_skill_f1.append(float(skills_cmp["f1"]))
        sample_bonus_f1.append(float(bonus_cmp["f1"]))
        for key in ("tp", "fp", "fn"):
            skills_total[key] += len(skills_cmp[key])
            bonus_total[key] += len(bonus_cmp[key])
        normalized_gold_title = normalize_position_name(row["review_gold_title"])
        normalized_pred_title = normalize_position_name(result.position_name)
        title_match = normalized_gold_title == normalized_pred_title
        title_hits += int(title_match)
        title_raw_exact = row["review_gold_title"] == result.position_name
        title_raw_hits += int(title_raw_exact)
        gold_education = row.get("review_gold_education", "").strip() or None
        predicted_education = (result.education.level if result.education else None) or None
        education_match = gold_education == predicted_education
        education_hits += int(education_match)
        predictions.append({
            "sample_id": row["sample_id"], "source": row["source"], "source_id": row["source_id"],
            "source_url": row["source_url"], "job_title_raw": row["job_title_raw"],
            "human_gold": {key: row.get(key, "") for key in LABEL_COLUMNS},
            "execution_status": "real_llm_success",
            "model_raw_output_pre_postprocess": _to_jsonable(tracker.model_output),
            "model_normalized_output": _to_jsonable(result),
            "comparison": {
                "title_raw_exact": title_raw_exact,
                "title_normalized_gold": normalized_gold_title,
                "title_normalized_prediction": normalized_pred_title,
                "title_normalized_match": title_match,
                "skills": skills_cmp, "bonus_skills": bonus_cmp,
                "required_bonus_mixing": {
                    "pred_required_matches_gold_bonus": sorted(set(predicted_skills) & set(normalized_gold_bonus)),
                    "pred_bonus_matches_gold_required": sorted(set(predicted_bonus) & set(normalized_gold_skills)),
                },
                "conditional_text_marker": any(marker in row["detail_raw_text"] for marker in ("优先", "一项或多项", "一项或", "任一", "或者")),
                "experience": "Schema coverage gap: JDExtractionResult has no experience_range field",
                "education_gold": gold_education,
                "education_prediction": predicted_education,
                "education_raw_exact": education_match,
                "education_empty_gold_is_null": gold_education is None,
                "core_duties": "Schema coverage gap: JDExtractionResult has no core_duties field",
            },
        })
    success_count = len([p for p in predictions if p["execution_status"] == "real_llm_success"])
    if not success_count:
        reasons = [p.get("fallback_reason") or p.get("failure_reason") for p in predictions]
        raise RuntimeError("12 条均未取得真实 LLM 输出；" + " | ".join(str(x) for x in reasons if x))
    metrics = {
        "total_samples": len(rows),
        "real_llm_success_samples": success_count,
        "fallback_samples": fallback_samples,
        "failed_samples": failed_samples,
        "title_raw_exact_accuracy": title_raw_hits / success_count,
        "title_normalized_accuracy": title_hits / success_count,
        "skills_micro": _metric(**skills_total),
        "skills_average_sample_f1": sum(sample_skill_f1) / success_count,
        "bonus_skills_micro": _metric(**bonus_total),
        "bonus_skills_average_sample_f1": sum(sample_bonus_f1) / success_count,
        "education_raw_exact_accuracy": education_hits / success_count,
        "experience": "Schema coverage gap: current JDExtractionResult schema has no experience_range field",
        "core_duties": "Schema coverage gap: current JDExtractionResult schema has no core_duties field",
    }
    (output_dir / "manual_jd_eval_predictions.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in predictions) + "\n", encoding="utf-8"
    )
    with (output_dir / "manual_jd_eval_cases.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=["sample_id", "execution_status", "job_title_raw", "gold_title", "predicted_title", "title_raw_exact", "title_normalized_match", "skills_tp", "skills_fp", "skills_fn", "skills_f1", "bonus_tp", "bonus_fp", "bonus_fn", "bonus_f1", "education_gold", "education_prediction", "education_raw_exact", "fallback_or_failure_reason", "experience_note", "core_duties_note"])
        writer.writeheader()
        for item in predictions:
            if item["execution_status"] != "real_llm_success":
                writer.writerow({"sample_id": item["sample_id"], "execution_status": item["execution_status"], "job_title_raw": item["job_title_raw"], "fallback_or_failure_reason": item.get("fallback_reason") or item.get("failure_reason", "")})
                continue
            cmp = item["comparison"]
            writer.writerow({"sample_id": item["sample_id"], "execution_status": item["execution_status"], "job_title_raw": item["job_title_raw"], "gold_title": item["human_gold"]["review_gold_title"], "predicted_title": item["model_normalized_output"]["position_name"], "title_raw_exact": cmp["title_raw_exact"], "title_normalized_match": cmp["title_normalized_match"], "skills_tp": "|".join(cmp["skills"]["tp"]), "skills_fp": "|".join(cmp["skills"]["fp"]), "skills_fn": "|".join(cmp["skills"]["fn"]), "skills_f1": cmp["skills"]["f1"], "bonus_tp": "|".join(cmp["bonus_skills"]["tp"]), "bonus_fp": "|".join(cmp["bonus_skills"]["fp"]), "bonus_fn": "|".join(cmp["bonus_skills"]["fn"]), "bonus_f1": cmp["bonus_skills"]["f1"], "education_gold": cmp["education_gold"], "education_prediction": cmp["education_prediction"], "education_raw_exact": cmp["education_raw_exact"], "experience_note": cmp["experience"], "core_duties_note": cmp["core_duties"]})
    (output_dir / "manual_jd_eval_report.md").write_text(
        "# A01 人工 JD 集端到端评测报告\n\n"
        "本报告只把 `real_llm_success` 行计入指标；`fallback` 和 `failed` 行保留在逐条结果中，但绝不计入指标。\n\n"
        "## 当前真实链路\n\n`JDExtractor.extract` → `LLMProviderChain.extract_structured` → `post_process`。岗位名归一化只用于评测侧的 `normalize_position_name` 对照；`PositionAligner`（Neo4j/SBERT）不在 `JDExtractor.extract` 内。\n\n"
        f"## 指标\n\n```json\n{json.dumps(metrics, ensure_ascii=False, indent=2)}\n```\n\n"
        "空学历 gold 以 `null / No explicit education requirement` 参与 raw exact 对比：模型同样未输出学历即为正确，凭空输出学历即为错误。经验与核心职责没有对应的 `JDExtractionResult` 字段，属于 Schema coverage gap，不会伪造预测或准确率。`skills` 与 `requirements[nice]` 分别作为必备技能和加分技能的可观测输出；该映射应在后续算法评审中确认。历史 0.6112 未参与本报告。\n",
        encoding="utf-8",
    )
    success_predictions = [p for p in predictions if p["execution_status"] == "real_llm_success"]
    error_counts: Counter[str] = Counter()
    case_lines: list[str] = []
    for item in predictions:
        if item["execution_status"] != "real_llm_success":
            reason = item.get("fallback_reason") or item.get("failure_reason", "")
            case_lines.append(f"- {item['sample_id']}: `{item['execution_status']}` — {reason}")
            continue
        cmp = item["comparison"]
        skill = cmp["skills"]
        bonus = cmp["bonus_skills"]
        mix = cmp["required_bonus_mixing"]
        if skill["fp"]:
            error_counts["model-added skills not in human gold"] += 1
        if skill["fn"]:
            error_counts["human-gold skills missed"] += 1
        if skill["fp"] and skill["fn"]:
            error_counts["skills have both additions and omissions"] += 1
        if mix["pred_required_matches_gold_bonus"] or mix["pred_bonus_matches_gold_required"]:
            error_counts["required/bonus skill mixing"] += 1
        if not cmp["title_raw_exact"] and cmp["title_normalized_match"]:
            error_counts["title normalization masks a raw-title difference (manual over-normalization check needed)"] += 1
        if cmp["conditional_text_marker"] and (skill["fp"] or skill["fn"] or bonus["fp"] or bonus["fn"]):
            error_counts["possible priority/OR-condition interpretation issue (text marker + set difference)"] += 1
        case_lines.append(
            f"- {item['sample_id']}: title raw={cmp['title_raw_exact']}, normalized={cmp['title_normalized_match']}; "
            f"skills TP/FP/FN={skill['tp']}/{skill['fp']}/{skill['fn']}, F1={skill['f1']:.4f}; "
            f"bonus TP/FP/FN={bonus['tp']}/{bonus['fp']}/{bonus['fn']}, F1={bonus['f1']:.4f}; "
            f"education={cmp['education_raw_exact']}"
        )
    lowest = sorted(success_predictions, key=lambda p: p["comparison"]["skills"]["f1"])[:3]
    lowest_lines = [
        f"- {p['sample_id']}: skills F1={p['comparison']['skills']['f1']:.4f}; "
        f"FP={p['comparison']['skills']['fp']}; FN={p['comparison']['skills']['fn']}"
        for p in lowest
    ]
    error_lines = [f"- {name}: {count}" for name, count in error_counts.most_common()] or ["- no automatically classifiable set-comparison error"]
    with (output_dir / "manual_jd_eval_report.md").open("a", encoding="utf-8") as fh:
        fh.write(
            "\n## Per-JD results\n\n" + "\n".join(case_lines) +
            "\n\n## Lowest three skill-F1 cases\n\n" + "\n".join(lowest_lines) +
            "\n\n## Main automatically classifiable error types\n\n" + "\n".join(error_lines) +
            "\n\nPriority/OR conditions are only flagged when a deterministic text marker co-occurs with a set difference; "
            "the current schema does not encode condition logic, so these are review candidates rather than conclusive errors.\n"
        )
    return metrics


def write_blocker_report(output_dir: Path, validation: dict[str, Any], xlsx: Path) -> None:
    issue_summary = Counter(issue["field"] for issue in validation["issues"])
    runtime_blocker = validation.get("runtime_blocker")
    if runtime_blocker:
        conclusion = (
            "预检已通过并已显式触发真实 `--run`，但生产抽取链在逐条抽取前初始化失败。"
            "未调用模型、未产生 fallback，也未生成模拟预测或指标。"
        )
    else:
        conclusion = (
            "本次未调用真实 LLM，也未生成预测、混淆矩阵或 F1。原因是盲标表未通过评测输入质量门槛；"
            "在此状态下运行会把不完整或格式错误的人工标签当作 gold，结果不可解释。"
        )
    lines = [
        "# 人工 JD 端到端评测阻塞报告",
        "",
        "## 结论",
        "",
        conclusion,
        "",
        "## 预检结果",
        "",
        f"- 工作簿：`{xlsx}`",
        f"- `Round1盲标` 数据行数：{validation['row_count']}/12",
        f"- 非空正文：{validation['rows_with_nonempty_detail']}/12；可追溯 URL：{validation['rows_with_source_url']}/12",
        f"- annotator：{', '.join(repr(x) for x in validation['observed_annotators']) or '空'}；要求为 12 条非空且一致",
        f"- 全字段格式合格且可纳入真实评测的行数：{validation['fully_valid_rows']}/12",
        f"- total_samples = {validation['row_count']}",
        "- real_llm_success_samples = 0；fallback_samples = 0；failed_samples = 0（未进入逐条抽取）",
        "",
        "## 格式异常汇总",
        "",
    ]
    lines.extend([f"- `{field}`：{count} 项" for field, count in sorted(issue_summary.items())] or ["- 无"])
    if runtime_blocker:
        lines += ["", "## 运行时阻塞", "", f"- `{runtime_blocker}`", "- 当前运行时无法导入 PyYAML；未验证 provider/API 配置，且未读取任何密钥。"]
    lines += [
        "",
        "## 真实链路审计",
        "",
        "- 实际入口为 `backend/app/services/extraction/jd_extractor.py:JDExtractor.extract`。",
        "- 真实路径为 `TASK_TEMPLATE + SYSTEM_PROMPT + FEW_SHOT_EXAMPLES` → `LLMProviderChain.extract_structured` → `post_process`。",
        "- `JDExtractor` 在配置缺失或 `LLMExtractionError` 时静默降级到 `_rule_based_extract`（白名单扫描）。因此不能把降级结果称为真实 LLM 端到端评测，更不能与历史 0.6112 白名单扫描数字混为一谈。",
        "- `JDExtractionResult` 有岗位名、skills、education、requirements（must/nice），但没有经验区间或核心职责字段；当前代码无法对这两项产出真实预测。",
        "- `PositionAligner` 的 Neo4j/SBERT 对齐不在 `JDExtractor.extract` 调用链；本评测脚本仅按要求用 `normalize_position_name` 做静态规则对照。",
        "",
        "## 恢复条件与命令",
        "",
        "1. 确保 12 条 annotator 非空且一致；将非空的 skills、bonus_skills、core_duties 填为 JSON 字符串数组；experience 留空或写 JSON 对象。空学历表示无明确学历要求，合法且不需要补写。",
        "2. 不改变现有标签含义的前提下，重新保存工作簿后运行预检。",
        "3. 只有预检为 12/12 后，才执行真实 LLM 调用。该命令可能产生模型调用费用：",
        "",
        "```powershell",
        "cd backend",
        "uv run python tests/evaluate/run_manual_jd_eval.py --run",
        "```",
        "",
        "预检命令（不调用网络或 LLM）：",
        "",
        "```powershell",
        "cd backend",
        "uv run python tests/evaluate/run_manual_jd_eval.py",
        "```",
    ]
    (output_dir / "evaluation_blocker_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_validation(output_dir: Path, validation: dict[str, Any]) -> str | None:
    """Persist preflight when possible, without blocking an explicitly requested run."""
    try:
        (output_dir / "manual_jd_eval_data_validation.json").write_text(
            json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def write_runtime_blocker_fallback(output_dir: Path, validation: dict[str, Any], reason: str) -> None:
    """Keep a durable diagnosis if an editor has locked the canonical report."""
    text = "\n".join([
        "# 手工 JD 评测运行时阻塞记录",
        "",
        "## 本次状态",
        "",
        "- total_samples = 12",
        "- real_llm_success_samples = 0",
        "- fallback_samples = 0",
        "- failed_samples = 0（未进入逐条抽取；这是启动级阻塞）",
        f"- annotator = {', '.join(validation.get('observed_annotators', [])) or 'unknown'}",
        f"- preflight = {'passed' if validation.get('ready_for_real_run') else 'failed'}",
        "",
        "## 实际阻塞原因",
        "",
        f"真实 `--run` 已触发，但在导入生产 `LLMProviderChain` 时失败：`{reason}`。因此当前运行时缺少 PyYAML，尚未初始化 JDExtractor，未发出模型请求，也未发生规则 fallback。",
        "",
        "## 恢复步骤",
        "",
        "在具备项目依赖的 Python 环境中安装/同步 `backend/pyproject.toml` 的依赖（至少包括 PyYAML、Pydantic、instructor、OpenAI），并配置有效且启用的 `configs/llm_providers.yaml` provider/API key。不要将 key 写入本报告。然后执行：",
        "",
        "```powershell",
        "cd backend",
        "uv run python tests/evaluate/run_manual_jd_eval.py --run",
        "```",
        "",
        "历史 0.6112 未被使用，也不是本次真实 LLM 指标。",
    ])
    (output_dir / "evaluation_runtime_blocker_report.md").write_text(text + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--xlsx", type=Path, default=DEFAULT_XLSX)
    parser.add_argument("--sheet", default="Round1盲标")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--run", action="store_true", help="Only after preflight succeeds, call the real LLM chain.")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = _load_round1_blind_rows(args.xlsx, args.sheet)
    validation = validate_rows(rows)
    validation_write_warning = write_validation(args.output_dir, validation)
    if validation_write_warning:
        print(f"WARNING: could not update validation summary: {validation_write_warning}")
    if not validation["ready_for_real_run"]:
        write_blocker_report(args.output_dir, validation, args.xlsx)
        print("BLOCKED: blind-review labels did not pass preflight; no LLM call was made.")
        return 2
    if not args.run:
        print("READY: preflight passed. Re-run with --run to call the real LLM chain.")
        return 0
    try:
        run_real_eval(rows, args.output_dir)
    except Exception as exc:
        validation["runtime_blocker"] = _safe_exception_summary(exc)
        validation_write_warning = write_validation(args.output_dir, validation)
        if validation_write_warning:
            validation["validation_write_warning"] = validation_write_warning
        try:
            write_blocker_report(args.output_dir, validation, args.xlsx)
        except OSError as report_exc:
            print(f"WARNING: could not update blocker report: {type(report_exc).__name__}: {report_exc}")
            try:
                write_runtime_blocker_fallback(args.output_dir, validation, validation["runtime_blocker"])
            except OSError as fallback_exc:
                print(f"WARNING: could not write runtime blocker fallback: {type(fallback_exc).__name__}: {fallback_exc}")
        print(f"BLOCKED: {_safe_exception_summary(exc)}")
        return 3
    print("SUCCESS: real LLM predictions and metrics were written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
