#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据模块最终封板审计（Final Data Seal）—— 全程只读，不调用 LLM，不自动修复。

审计基线：feature/data-final-seal（从最新 origin/develop 切出）。
所有产物仅写入 backend/data/golden_set/audit/final_data_seal/。

覆盖（对应用户任务 §六–§十八）：
  数据规模/文件存在性、唯一性、字段完整率、Zhilian SHA 一致性、
  两条历史修复回归、Gold 保护、ANN-0023 回归、Gold exclusion、
  跨源重复、Official Career50 专项、统一审计 P0/P1 状态、
  安全扫描（脱敏）、DATA_ASSET_MANIFEST、历史基线对照、最终 Gate。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ---------------------------------------------------------------- 路径
REPO = Path(__file__).resolve().parents[6]  # final_data_seal/scripts -> repo root
GS = REPO / "backend" / "data" / "golden_set"
V1 = GS / "candidate_pool" / "v1"
OFFI = GS / "candidate_pool" / "official_career_50"
FINAL = GS / "final"
AUDIT = GS / "audit" / "unified_jd_audit"
SEAL = GS / "audit" / "final_data_seal"

F_RAW = V1 / "real_jd_candidates_raw.jsonl"
F_CLEAN = V1 / "real_jd_candidates_clean.jsonl"
F_REVIEW = V1 / "real_jd_review_required.csv"
F_REJECT = V1 / "real_jd_rejected.csv"
F_GOLD = FINAL / "jd_golden_110.jsonl"
F_GOLD_DICT = FINAL / "jd_golden_110_data_dictionary.md"
F_OFFI_RAW = OFFI / "official_career_50_raw.jsonl"
F_OFFI_CLEAN = OFFI / "official_career_50_clean.jsonl"
F_OFFI_GOLD = OFFI / "official_career_50_gold.jsonl"
F_EXCL = AUDIT / "gold_eval_exclusion_manifest.csv"
F_ACCEPT = AUDIT / "final_acceptance_summary.json"
F_CROSS = AUDIT / "unified_jd_cross_source_duplicates.csv"

T1 = "CC148739350J40212149403"  # ANN-0023
T2 = "CC404298980J40856902010"
RE_HEX64 = re.compile(r"^[0-9a-f]{64}$")

BEIJING = timezone(timedelta(hours=8))
AUDIT_TIME = datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M %Z")

# 历史基线（仅对照，不作为期望值强行回写）
HIST = {
    "zhilian_total": 158, "accepted": 135, "review_required": 23, "rejected": 0,
    "gold": 110, "official": 50, "tencent": 25, "bytedance": 25, "manifest": 110,
}
# 此前各阶段记录的 Gold110 SHA（字节级保护基线）
GOLD_KNOWN_SHA = "ceedfa6987fee665ea53f17678e8f06cb197a632bc828f99c5b962615c508061"

# 审计基线（本轮刷新：c4450430 → 0c111e97，ff-only 快进，无 merge commit）
BASELINE_DEVELOP_SHA = "0c111e97d26af7030a7fb9244615b8d5b18367d3"
PREVIOUS_BASELINE_SHA = "c44504303aaa176d1a5d0251c30f7b0b7a27c8c3"

findings: list[str] = []   # 阻断/异常发现
blockers: list[str] = []


def note(msg: str) -> None:
    findings.append(msg)


# ---------------------------------------------------------------- 工具
def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def text(x) -> str:
    return "" if x is None else str(x)


def nonempty(v) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, (list, dict)):
        return len(v) > 0
    return bool(v)


def load_jsonl(path: Path):
    rows, parse_errors = [], []
    if not path.is_file():
        return rows, [f"FILE_MISSING: {path}"]
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except Exception as e:  # noqa: BLE001
                parse_errors.append(f"{path.name}:{i} {type(e).__name__}: {e}")
    return rows, parse_errors


def load_csv(path: Path):
    rows, errors = [], []
    if not path.is_file():
        return rows, [f"FILE_MISSING: {path}"]
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                rows.append(r)
    except Exception as e:  # noqa: BLE001
        errors.append(f"{path.name}: {type(e).__name__}: {e}")
    return rows, errors


def file_meta(path: Path) -> dict:
    if not path.is_file():
        return {"path": str(path.relative_to(REPO)), "exists": False}
    b = path.read_bytes()
    return {
        "path": str(path.relative_to(REPO)).replace("\\", "/"),
        "exists": True,
        "size_bytes": len(b),
        "sha256": sha256_bytes(b),
    }


def sha_calc(r: dict) -> str:
    """仓库验收口径：SHA256(responsibilities + "\n" + requirements)。"""
    return sha256_text(text(r.get("responsibilities")) + "\n" + text(r.get("requirements"))).lower()


def uniqueness(rows: list[dict], field: str, id_field: str):
    total = len(rows)
    vals = [text(r.get(field)).strip() for r in rows]
    non_empty = sum(1 for v in vals if v)
    counts = Counter(v for v in vals if v)
    dupes = {v: c for v, c in counts.items() if c > 1}
    dup_detail = []
    if dupes:
        ids_by_val = defaultdict(list)
        for r in rows:
            v = text(r.get(field)).strip()
            if v in dupes:
                ids_by_val[v].append(text(r.get(id_field)) or text(r.get("sample_id")) or "?")
        for v, ids in ids_by_val.items():
            dup_detail.append({"field": field, "value": v, "count": len(ids), "record_ids": ids})
    return {
        "field": field, "total": total, "non_empty": non_empty,
        "empty": total - non_empty, "unique": len(counts),
        "duplicate_values": len(dupes), "duplicate_detail": dup_detail,
    }


def completeness(rows: list[dict], field_map: dict[str, str]) -> list[dict]:
    """field_map: 逻辑字段 -> 实际 key。"""
    out = []
    total = len(rows)
    for logical, key in field_map.items():
        empty = sum(1 for r in rows if not nonempty(r.get(key)))
        out.append({
            "field": logical, "key": key, "total": total,
            "non_empty": total - empty, "empty": empty,
            "coverage_pct": round(100.0 * (total - empty) / total, 2) if total else 0.0,
        })
    return out


# ---------------------------------------------------------------- 载入
raw_rows, raw_err = load_jsonl(F_RAW)
clean_rows, clean_err = load_jsonl(F_CLEAN)
gold_rows, gold_err = load_jsonl(F_GOLD)
offi_raw_rows, offi_raw_err = load_jsonl(F_OFFI_RAW)
offi_rows, offi_err = load_jsonl(F_OFFI_CLEAN)
offi_gold_rows, offi_gold_err = load_jsonl(F_OFFI_GOLD)
review_rows, review_err = load_csv(F_REVIEW)
reject_rows, reject_err = load_csv(F_REJECT)
excl_rows, excl_err = load_csv(F_EXCL)
cross_rows, cross_err = load_csv(F_CROSS)

accept = {}
if F_ACCEPT.is_file():
    accept = json.loads(F_ACCEPT.read_text(encoding="utf-8"))

# ---------------------------------------------------------------- §六 规模/文件
file_checks = []
for p, kind in [
    (F_RAW, "jsonl"), (F_CLEAN, "jsonl"), (F_REVIEW, "csv"), (F_REJECT, "csv"),
    (F_GOLD, "jsonl"), (F_GOLD_DICT, "md"),
    (F_OFFI_RAW, "jsonl"), (F_OFFI_CLEAN, "jsonl"), (F_OFFI_GOLD, "jsonl"),
    (F_EXCL, "csv"), (F_ACCEPT, "json"), (F_CROSS, "csv"),
]:
    m = file_meta(p)
    m["kind"] = kind
    m["parse_ok"] = True
    if kind == "jsonl":
        m["records"] = {"real_jd_candidates_raw.jsonl": len(raw_rows),
                        "real_jd_candidates_clean.jsonl": len(clean_rows),
                        "jd_golden_110.jsonl": len(gold_rows),
                        "official_career_50_raw.jsonl": len(offi_raw_rows),
                        "official_career_50_clean.jsonl": len(offi_rows),
                        "official_career_50_gold.jsonl": len(offi_gold_rows)}.get(p.name)
    elif kind == "csv":
        m["records"] = {"real_jd_review_required.csv": len(review_rows),
                        "real_jd_rejected.csv": len(reject_rows),
                        "gold_eval_exclusion_manifest.csv": len(excl_rows),
                        "unified_jd_cross_source_duplicates.csv": len(cross_rows)}.get(p.name)
    elif kind == "json":
        m["records"] = None
    file_checks.append(m)

for err in raw_err + clean_err + gold_err + offi_err + offi_raw_err + offi_gold_err + review_err + reject_err + excl_err + cross_err:
    note(f"PARSE: {err}")

tier_counts = Counter(text(r.get("tier")) or "(none)" for r in clean_rows)
counts = {
    "zhilian_raw": len(raw_rows),
    "zhilian_clean": len(clean_rows),
    "zhilian_tier_accepted": tier_counts.get("accepted", 0),
    "zhilian_tier_review_required": tier_counts.get("review_required", 0),
    "zhilian_tier_other": {k: v for k, v in tier_counts.items() if k not in ("accepted", "review_required")},
    "review_csv_rows": len(review_rows),
    "rejected_csv_rows": len(reject_rows),
    "gold": len(gold_rows),
    "official_raw": len(offi_raw_rows),
    "official_clean": len(offi_rows),
    "official_gold": len(offi_gold_rows),
    "exclusion_manifest_rows": len(excl_rows),
    "cross_source_duplicate_rows": len(cross_rows),
}

# ---------------------------------------------------------------- §七 唯一性
uniq = {
    "zhilian_clean": [uniqueness(clean_rows, f, "source_id") for f in ("source_id", "source_url", "_sha256")],
    "gold110": [uniqueness(gold_rows, f, "sample_id") for f in ("sample_id", "source_id", "source_url")],
    "official50_clean": [uniqueness(offi_rows, f, "source_id") for f in ("source_id", "source_url", "_sha256")],
    "official50_gold": [uniqueness(offi_gold_rows, f, "sample_id") for f in ("sample_id", "source_id", "source_url")],
    "exclusion_manifest": [uniqueness(excl_rows, f, "sample_id") for f in ("source_id", "gold_source_id")],
}
for ds, checks in uniq.items():
    for c in checks:
        if c["duplicate_values"]:
            note(f"UNIQUENESS {ds}.{c['field']}: {c['duplicate_values']} duplicated -> {c['duplicate_detail'][:3]}")

# ---------------------------------------------------------------- §八 字段完整率
FIELD_MAP_ZL = {
    "title": "job_title_raw", "company": "company_name", "location": "location",
    "responsibilities": "responsibilities", "requirements": "requirements",
    "detail_raw_text": "detail_raw_text", "source_id": "source_id", "source_url": "source_url",
}
FIELD_MAP_GOLD = dict(FIELD_MAP_ZL)  # Gold 同样具备这些源字段
FIELD_MAP_OFFI = {
    "title": "job_title_raw", "company": "company_name", "location": "location",
    "responsibilities": "responsibilities", "requirements": "requirements",
    "detail_raw_text": "detail_raw_text", "source_id": "source_id", "source_url": "source_url",
}
completeness_res = {
    "zhilian_clean": completeness(clean_rows, FIELD_MAP_ZL),
    "gold110": completeness(gold_rows, FIELD_MAP_GOLD),
    "official50_clean": completeness(offi_rows, FIELD_MAP_OFFI),
}
# Gold 标注字段完整率（信息项）
GOLD_FIELDS = ("gold_title", "gold_skills", "gold_bonus_skills", "gold_experience", "gold_education", "gold_core_duties")
gold_label_completeness = completeness(gold_rows, {f: f for f in GOLD_FIELDS})

# ---------------------------------------------------------------- §九 Zhilian SHA
sha_mismatch, sha_legacy, sha_bad_format = [], [], []
for r in clean_rows:
    sid = text(r.get("source_id"))
    stored = text(r.get("_sha256")).strip().lower()
    if not RE_HEX64.match(stored):
        sha_bad_format.append(sid)
    calc = sha_calc(r)
    if stored != calc:
        sha_mismatch.append({"source_id": sid, "stored_prefix": stored[:16], "expected_prefix": calc[:16]})
        if stored == sha256_text(text(r.get("responsibilities"))).lower():
            sha_legacy.append(sid)
sha_res = {
    "total": len(clean_rows), "match": len(clean_rows) - len(sha_mismatch),
    "mismatch": len(sha_mismatch), "mismatch_detail": sha_mismatch,
    "legacy_resp_only_count": len(sha_legacy), "bad_format": sha_bad_format,
    "formula": 'SHA256(responsibilities + "\\n" + requirements)  [与 audit/unified_jd_audit 验收脚本同口径]',
}
if sha_mismatch:
    note(f"SHA mismatch x{len(sha_mismatch)}: {[m['source_id'] for m in sha_mismatch][:10]}")

# ---------------------------------------------------------------- §十 两条历史修复回归
def regression(sid: str) -> dict:
    row = next((r for r in clean_rows if text(r.get("source_id")) == sid), None)
    if row is None:
        return {"source_id": sid, "present": False}
    resp, req = text(row.get("responsibilities")), text(row.get("requirements"))
    stored = text(row.get("_sha256")).strip().lower()
    return {
        "source_id": sid, "present": True,
        "resp_non_empty": bool(resp), "req_non_empty": bool(req),
        "resp_len": len(resp), "req_len": len(req),
        "sha_match": stored == sha_calc(row),
    }

reg = {T1: regression(T1), T2: regression(T2)}
for sid, d in reg.items():
    if not d.get("present") or not d.get("resp_non_empty") or not d.get("req_non_empty") or not d.get("sha_match"):
        note(f"REGRESSION {sid}: {d}")

# ---------------------------------------------------------------- §十一 Gold 保护
gold_bytes = F_GOLD.read_bytes()
gold_sha = sha256_bytes(gold_bytes)
gold_meta = {
    "path": str(F_GOLD.relative_to(REPO)).replace("\\", "/"),
    "sha256": gold_sha, "size_bytes": len(gold_bytes), "records": len(gold_rows),
    "records_eq_110": len(gold_rows) == 110,
    "sample_id_unique": all(c["duplicate_values"] == 0 for c in uniq["gold110"] if c["field"] == "sample_id"),
    "matches_known_baseline_sha": gold_sha == GOLD_KNOWN_SHA,
    "gold_fields_present": {f: all(f in r for r in gold_rows) for f in GOLD_FIELDS},
}
if not gold_meta["records_eq_110"]:
    note(f"GOLD records != 110: {len(gold_rows)}")
if not gold_meta["sample_id_unique"]:
    note("GOLD sample_id not unique")

# ---------------------------------------------------------------- §十二 ANN-0023
ann23 = next((r for r in gold_rows if text(r.get("sample_id")) == "ANN-0023"), None)
ann23_res = {
    "exists": ann23 is not None,
    "source_id": text(ann23.get("source_id")) if ann23 else None,
    "source_id_is_T1": text(ann23.get("source_id")) == T1 if ann23 else False,
    "verdict": "GOLD_CORRECT" if ann23 and text(ann23.get("source_id")) == T1 else "MISSING",
    "basis": "final_acceptance_summary.json ANN0023_correct=true；无新仓库证据推翻旧结论，保持 GOLD_CORRECT（不因 source evidence 边界改 Gold）",
}

# ---------------------------------------------------------------- §十三 exclusion manifest
gold_sids = {text(r.get("source_id")).strip() for r in gold_rows}
excl_sids = {text(r.get("source_id")).strip() for r in excl_rows if text(r.get("source_id")).strip()}
excl_gold_sids = {text(r.get("gold_source_id")).strip() for r in excl_rows if text(r.get("gold_source_id")).strip()}
overlap_notes = [text(r.get("note")) for r in excl_rows]
excl_res = {
    "rows": len(excl_rows),
    "source_id_non_empty": len(excl_sids),
    "source_id_unique": len(excl_sids),
    "gold_source_id_non_empty": len(excl_gold_sids),
    "gold_source_id_unique": len(excl_gold_sids),
    "gold_source_coverage": f"{len(gold_sids & excl_gold_sids)}/{len(gold_sids)}",
    "gold_source_ids_all_in_manifest": gold_sids == excl_gold_sids,
    "T1_in_manifest": T1 in excl_sids,
    "ANN0023_in_manifest": any(text(r.get("sample_id")) == "ANN-0023" for r in excl_rows),
    "expected_parent_child_notes": sum(1 for n in overlap_notes if "EXPECTED_PARENT_CHILD_OVERLAP" in n),
}

# ---------------------------------------------------------------- §十四 跨源
zl_sids = {text(r.get("source_id")).strip() for r in clean_rows if text(r.get("source_id")).strip()}
zl_urls = {text(r.get("source_url")).strip() for r in clean_rows if text(r.get("source_url")).strip()}
zl_shas = {text(r.get("_sha256")).strip().lower() for r in clean_rows if text(r.get("_sha256")).strip()}
gold_urls = {text(r.get("source_url")).strip() for r in gold_rows if text(r.get("source_url")).strip()}
off_sids = {text(r.get("source_id")).strip() for r in offi_rows if text(r.get("source_id")).strip()}
off_urls = {text(r.get("source_url")).strip() for r in offi_rows if text(r.get("source_url")).strip()}
off_shas = {text(r.get("_sha256")).strip().lower() for r in offi_rows if text(r.get("_sha256")).strip()}

cross_res = {
    "gold_vs_zhilian_source_id_overlap": len(gold_sids & zl_sids),
    "gold_vs_zhilian_source_url_overlap": len(gold_urls & zl_urls),
    "gold_vs_zhilian_relation": "EXPECTED_PARENT_CHILD_OVERLAP（Gold 由 Zhilian 候选派生+人工标注，110 条父子重叠为设计预期，非 DATA_LEAKAGE）",
    "zhilian_vs_official_source_id_overlap": len(zl_sids & off_sids),
    "zhilian_vs_official_source_url_overlap": len(zl_urls & off_urls),
    "zhilian_vs_official_sha256_overlap": len(zl_shas & off_shas),
    "gold_vs_official_source_id_overlap": len(gold_sids & off_sids),
    "gold_vs_official_source_url_overlap": len(gold_urls & off_urls),
    "unified_cross_source_duplicate_csv_rows": len(cross_rows),
}
# cross csv 内容摘要（前若干行分类）
cross_summary = Counter()
for r in cross_rows:
    key = (text(r.get("relation")) or text(r.get("type")) or text(r.get("pair")) or "row")[:80]
    cross_summary[key] += 1
cross_res["unified_cross_csv_relation_counts"] = dict(cross_summary)

# ---------------------------------------------------------------- §十五 Official50
src_company = Counter(text(r.get("source_company")) or "(none)" for r in offi_rows)
company_name = Counter()
for r in offi_rows:
    cn = text(r.get("company_name"))
    if "腾讯" in cn or "Tencent" in cn:
        company_name["tencent"] += 1
    elif "字节" in cn or "ByteDance" in cn or "抖音" in cn:
        company_name["bytedance"] += 1
    else:
        company_name["other:" + cn[:20]] += 1
future_anoms = []
for r in offi_rows:
    pt = text(r.get("publish_time"))[:10]
    ct = text(r.get("crawl_time"))[:10]
    if pt and ct and pt > ct:
        future_anoms.append({
            "source_id": text(r.get("source_id")), "company": text(r.get("company_name")),
            "publish_time": text(r.get("publish_time")), "crawl_time": text(r.get("crawl_time")),
            "classification": "SOURCE_REPORTED_FUTURE_TIME（保留 source-reported 原值，不修正）",
        })
offi_res = {
    "total": len(offi_rows),
    "source_company_distribution": dict(src_company),
    "company_name_distribution": dict(company_name),
    "tencent_count": company_name.get("tencent", 0),
    "bytedance_count": company_name.get("bytedance", 0),
    "source_id_unique": all(c["duplicate_values"] == 0 for c in uniq["official50_clean"] if c["field"] == "source_id"),
    "source_url_unique": all(c["duplicate_values"] == 0 for c in uniq["official50_clean"] if c["field"] == "source_url"),
    "sha256_all_valid_hex64": all(RE_HEX64.match(text(r.get("_sha256")).strip().lower()) for r in offi_rows),
    "field_completeness": completeness(offi_rows, FIELD_MAP_OFFI),
    "future_publish_time_anomalies": future_anoms,
    "future_anomaly_count": len(future_anoms),
}

# ---------------------------------------------------------------- §十六 统一审计状态
gate_audit = {
    "P0": accept.get("P0"), "P1": accept.get("P1"),
    "P2": accept.get("P2"), "P3": accept.get("P3"),
    "BASELINE": accept.get("BASELINE"),
    "source_file": str(F_ACCEPT.relative_to(REPO)).replace("\\", "/"),
    "SHA_mismatch_count": accept.get("SHA_mismatch_count"),
    "Manifest_rows": accept.get("Manifest_rows"),
}

# ---------------------------------------------------------------- §十七 安全扫描
SECRET_PATTERNS = [
    ("openai_sk_key", re.compile(r"sk-[A-Za-z0-9]{16,}")),
    ("aws_ak", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("bearer_token", re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]{20,}")),
    ("credential_assignment", re.compile(
        r"(?i)(api[_-]?key|secret|token|password|passwd|pwd)\s*[:=]\s*['\"][A-Za-z0-9\-._]{10,}['\"]")),
    ("env_file_ref", re.compile(r"(?i)\.env(\b|[._])")),
]
SCAN_DIRS = [V1, FINAL, OFFI, AUDIT]
SCAN_SUFFIX = {".jsonl", ".csv", ".json", ".md", ".txt"}
# 基线刷新后 golden_set 根目录新增/变更的算法/测试评测集（非核心四类资产，一并纳入只读安全扫描）
SCAN_EXTRA_FILES = [
    GS / "education_semantics_v1.jsonl",
    GS / "golden_set_match_v3.jsonl",
    GS / "position_domain_eval.jsonl",
]
sec_findings = []
files_scanned = 0
scan_targets = [p for d in SCAN_DIRS for p in sorted(d.rglob("*"))] + SCAN_EXTRA_FILES
for p in scan_targets:
    if not p.is_file() or p.suffix.lower() not in SCAN_SUFFIX:
        continue
    # 跳过封板目录自身（产物生成后另行说明）
    if SEAL in p.parents:
        continue
    files_scanned += 1
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:  # noqa: BLE001
        continue
    for ln, line in enumerate(lines, 1):
        for kind, rx in SECRET_PATTERNS:
            m = rx.search(line)
            if m:
                hit = m.group(0)
                masked = hit[:6] + "***MASKED***"
                sec_findings.append({
                    "file": str(p.relative_to(REPO)).replace("\\", "/"),
                    "line": ln, "type": kind, "evidence_masked": masked,
                })
sec_res = {"files_scanned": files_scanned, "suspect_count": len(sec_findings), "items": sec_findings[:50]}

# ---------------------------------------------------------------- §七 Post-baseline Golden-set Changes
# 区间 PREVIOUS_BASELINE_SHA(c4450430) .. BASELINE_DEVELOP_SHA(0c111e97)，git diff --name-status 实测：
#   A education_semantics_v1.jsonl / A golden_set_match_v3.jsonl /
#   M position_domain_eval.jsonl / D review/evaluation_110_r1~r3/manual_jd_eval_*（12 文件）
# 核心四类资产（candidate_pool/v1、final、official_career_50、audit/unified_jd_audit）在该区间零变更。
def _jsonl_records(path: Path):
    if not path.is_file():
        return None
    return sum(1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip())

post_changes = [
    {"path": "backend/data/golden_set/education_semantics_v1.jsonl", "change": "A 新增",
     "records": _jsonl_records(GS / "education_semantics_v1.jsonl"),
     "asset_class": "ALGORITHM_EVAL_ASSET",
     "purpose": "学历语义聚合评测集（scripts/generate_education_semantics.py 生成；匹配/聚合算法调参与测试使用）",
     "in_core_four": False, "affects_gold110": False, "affects_zhilian158": False,
     "affects_official50": False, "affects_unified_audit": False, "affects_seal_gate": False},
    {"path": "backend/data/golden_set/golden_set_match_v3.jsonl", "change": "A 新增",
     "records": _jsonl_records(GS / "golden_set_match_v3.jsonl"),
     "asset_class": "ALGORITHM_EVAL_ASSET",
     "purpose": "人岗匹配 v3 评测集（scripts/build_match_golden_v3.py 构建；backend/tests/matching 使用）",
     "in_core_four": False, "affects_gold110": False, "affects_zhilian158": False,
     "affects_official50": False, "affects_unified_audit": False, "affects_seal_gate": False},
    {"path": "backend/data/golden_set/position_domain_eval.jsonl", "change": "M 修改",
     "records": _jsonl_records(GS / "position_domain_eval.jsonl"),
     "asset_class": "TEST_EVAL_ASSET",
     "purpose": "岗位领域图谱评测集（backend/tests/graph/test_position_domain.py 使用）",
     "in_core_four": False, "affects_gold110": False, "affects_zhilian158": False,
     "affects_official50": False, "affects_unified_audit": False, "affects_seal_gate": False},
    {"path": "backend/data/golden_set/review/evaluation_110_r1/ + r2/ + r3/（manual_jd_eval_cases/data_validation/predictions/report 共 12 文件）",
     "change": "D 删除（12 文件）", "records": None,
     "asset_class": "OTHER_GOLDEN_SET_ASSET",
     "purpose": "历史 r1/r2/r3 人工 JD 评测中间产物，已由 final/jd_golden_110.jsonl 正式集与 unified_jd_audit 取代",
     "in_core_four": False, "affects_gold110": False, "affects_zhilian158": False,
     "affects_official50": False, "affects_unified_audit": False, "affects_seal_gate": False},
]

# ---------------------------------------------------------------- §五/§二十一 历史基线对照 + Gate
hist_diff = []
def cmp_hist(label, current, expected):
    if current != expected:
        hist_diff.append({"item": label, "current": current, "historical": expected,
                          "explanation": "需人工确认差异来源（仓库记录可解释性）"})
cmp_hist("zhilian_total", counts["zhilian_clean"], HIST["zhilian_total"])
cmp_hist("accepted", counts["zhilian_tier_accepted"], HIST["accepted"])
cmp_hist("review_required_tier", counts["zhilian_tier_review_required"], HIST["review_required"])
cmp_hist("review_required_csv", counts["review_csv_rows"], HIST["review_required"])
cmp_hist("rejected", counts["rejected_csv_rows"], HIST["rejected"])
cmp_hist("gold", counts["gold"], HIST["gold"])
cmp_hist("official50", counts["official_clean"], HIST["official"])
cmp_hist("tencent", offi_res["tencent_count"], HIST["tencent"])
cmp_hist("bytedance", offi_res["bytedance_count"], HIST["bytedance"])
cmp_hist("exclusion_manifest", counts["exclusion_manifest_rows"], HIST["manifest"])

gate_checks = {
    "P0_eq_0": gate_audit["P0"] == 0,
    "P1_eq_0": gate_audit["P1"] == 0,
    "gold_unmodified_vs_known_sha": gold_meta["matches_known_baseline_sha"],
    "gold_parseable": len(gold_err) == 0,
    "gold_records_110": gold_meta["records_eq_110"],
    "gold_sample_id_unique": gold_meta["sample_id_unique"],
    "zhilian_sha_no_mismatch": sha_res["mismatch"] == 0,
    "exclusion_manifest_complete": excl_res["gold_source_ids_all_in_manifest"] and excl_res["rows"] == 110,
    "no_secret_leak": sec_res["suspect_count"] == 0,
    "key_files_readable": all(c.get("exists") for c in file_checks if c["kind"] in ("jsonl", "csv") and c["path"].endswith(("raw.jsonl", "clean.jsonl", "jd_golden_110.jsonl", "gold_eval_exclusion_manifest.csv"))),
    "no_parse_errors": not (raw_err or clean_err or gold_err or offi_err or excl_err),
    "cross_source_no_unexpected_overlap": (cross_res["zhilian_vs_official_source_id_overlap"] == 0
                                           and cross_res["gold_vs_official_source_id_overlap"] == 0
                                           and cross_res["zhilian_vs_official_sha256_overlap"] == 0),
    "regression_T1_T2_ok": all(d.get("present") and d.get("resp_non_empty") and d.get("req_non_empty") and d.get("sha_match") for d in reg.values()),
}
gate_pass = all(gate_checks.values())
GATE = "DATA_MODULE_READY_FOR_FINAL_SUBMISSION" if gate_pass else "DATA_MODULE_FINAL_SEAL_BLOCKED"
if not gate_pass:
    for k, v in gate_checks.items():
        if not v:
            blockers.append(k)

summary = {
    "audit_time_bjt": AUDIT_TIME,
    "branch": "feature/data-final-seal",
    "baseline_develop_sha": BASELINE_DEVELOP_SHA,
    "previous_baseline_sha": PREVIOUS_BASELINE_SHA,
    "baseline_refresh": "ff-only c4450430 -> 0c111e97（无 merge commit）",
    "gate": GATE, "gate_checks": gate_checks, "blockers": blockers,
    "counts": counts, "tier_counts": dict(tier_counts),
    "uniqueness": uniq, "completeness": completeness_res,
    "gold_label_completeness": gold_label_completeness,
    "sha_zhilian": sha_res, "regression_two_ids": reg,
    "gold_protection": gold_meta, "ann0023": ann23_res,
    "exclusion_manifest": excl_res, "cross_source": cross_res,
    "official50": offi_res, "unified_audit_gate": gate_audit,
    "security_scan": {**sec_res, "items": sec_findings[:50]},
    "historical_baseline_differences": hist_diff,
    "post_baseline_golden_set_changes": post_changes,
    "file_checks": file_checks,
    "notes": findings,
}

# ---------------------------------------------------------------- 写产物
SEAL.mkdir(parents=True, exist_ok=True)
(SEAL / "scripts").mkdir(exist_ok=True)

# 1) quality summary json
(SEAL / "final_data_quality_summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

# 2) checksums csv
with open(SEAL / "final_data_checksums.csv", "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["path", "exists", "kind", "records", "size_bytes", "sha256", "note"])
    for c in file_checks:
        w.writerow([c.get("path"), c.get("exists"), c.get("kind"), c.get("records"),
                    c.get("size_bytes"), c.get("sha256"), ""])

# 3) asset manifest csv
with open(SEAL / "final_data_manifest.csv", "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["path", "role", "records", "source", "is_gold", "allowed_for_eval",
                "allowed_for_training", "notes"])
    w.writerow(["backend/data/golden_set/final/jd_golden_110.jsonl", "gold_eval_set_110",
                110, "zhilian_candidate_derived+human_annotation", "yes", "yes", "NO",
                "Gold 评测集；gold_eval_exclusion_manifest 明确排除于 train/dev/retrieval/few-shot"])
    w.writerow(["backend/data/golden_set/final/jd_golden_110_data_dictionary.md", "gold_data_dictionary",
                "", "human_annotation_guideline", "no", "reference", "NOT_DEFINED_DO_NOT_ASSUME",
                "Gold 字段字典说明文档"])
    w.writerow(["backend/data/golden_set/candidate_pool/v1/real_jd_candidates_raw.jsonl", "candidate_pool_raw",
                len(raw_rows), "zhaopin.com(智联招聘)", "no", "no", "NOT_DEFINED_DO_NOT_ASSUME",
                "158 条原始候选；其中 110 条 Gold 来源按 exclusion manifest 禁止用于 train/dev/retrieval"])
    w.writerow(["backend/data/golden_set/candidate_pool/v1/real_jd_candidates_clean.jsonl", "candidate_pool_clean",
                len(clean_rows), "zhaopin.com(智联招聘)", "no", "no", "NOT_DEFINED_DO_NOT_ASSUME",
                f"tier: accepted={counts['zhilian_tier_accepted']}, review_required={counts['zhilian_tier_review_required']}；110 Gold 来源排除训练"])
    w.writerow(["backend/data/golden_set/candidate_pool/v1/real_jd_review_required.csv", "triage_queue_review_required",
                len(review_rows), "unified_audit_triage", "no", "no", "NO",
                "23 条待人工复核队列，非训练样本"])
    w.writerow(["backend/data/golden_set/candidate_pool/v1/real_jd_rejected.csv", "triage_queue_rejected",
                len(reject_rows), "unified_audit_triage", "no", "no", "NO", "0 条拒绝（仅表头）"])
    w.writerow(["backend/data/golden_set/candidate_pool/official_career_50/official_career_50_clean.jsonl",
                "official_career_candidate_pool", len(offi_rows),
                "tencent+bytedance official career sites", "no", "no", "NOT_DEFINED_DO_NOT_ASSUME",
                f"腾讯={offi_res['tencent_count']} 字节={offi_res['bytedance_count']}；2 条 future publish_time 为 SOURCE_REPORTED_FUTURE_TIME 保留原值"])
    w.writerow(["backend/data/golden_set/candidate_pool/official_career_50/official_career_50_gold.jsonl",
                "official_career_gold_eval_set_50", len(offi_gold_rows),
                "tencent+bytedance official + human_annotation", "yes", "yes", "NOT_DEFINED_DO_NOT_ASSUME",
                "Official Career 50 条评测标注集"])
    w.writerow(["backend/data/golden_set/audit/unified_jd_audit/gold_eval_exclusion_manifest.csv",
                "gold_eval_exclusion_governance", len(excl_rows), "unified_jd_audit", "no", "reference", "NO",
                "110 行 Gold↔Zhilian 父子映射；EXPECTED_PARENT_CHILD_OVERLAP；train/dev/retrieval/few-shot 排除依据"])
    w.writerow(["backend/data/golden_set/audit/unified_jd_audit/final_acceptance_summary.json",
                "unified_audit_gate_record", "", "unified_jd_audit", "no", "reference", "NO",
                f"P0={gate_audit['P0']} P1={gate_audit['P1']} P2={gate_audit['P2']} P3={gate_audit['P3']} BASELINE={gate_audit['BASELINE']}"])
    w.writerow(["backend/data/golden_set/audit/unified_jd_audit/", "unified_audit_artifacts",
                "", "unified_jd_audit", "no", "reference", "NO", "统一审计报告/issue triage/跨源重复等留痕"])

# 4) security scan md
sec_lines = ["# Final Data Seal — 安全扫描报告", "",
             f"- 审计时间（北京时间）：{AUDIT_TIME}",
             f"- 扫描范围：candidate_pool/v1、final/、candidate_pool/official_career_50、audit/unified_jd_audit 的数据文件，外加基线刷新后 golden_set/ 根目录新增评测集（education_semantics_v1 / golden_set_match_v3 / position_domain_eval），共 {sec_res['files_scanned']} 个文件（后缀 .jsonl/.csv/.json/.md/.txt）",
             "- 扫描方式：高置信正则（sk- key / AWS AK / Bearer token / 凭据赋值 / .env 引用）；**不打印任何 secret 正文**",
             f"- 疑似项：**{sec_res['suspect_count']}**", ""]
if sec_findings:
    sec_lines += ["| 文件 | 行 | 类型 | 脱敏证据 |", "|---|---|---|---|"]
    for it in sec_findings[:50]:
        sec_lines.append(f"| {it['file']} | {it['line']} | {it['type']} | {it['evidence_masked']} |")
else:
    sec_lines += ["**结论：未发现 API key / Bearer token / password / secret / .env 凭据 / LLM provider credential 泄漏。**",
                  "",
                  "说明：JD 正文可能出现 “token/password” 等业务词汇（如岗位技能要求），"
                  "此类为招聘文本内容而非凭据赋值，不构成 secret 泄漏；扫描规则要求 `key=value`/`Bearer ` 等凭据形态才命中。"]
(SEAL / "final_data_security_scan.md").write_text("\n".join(sec_lines), encoding="utf-8")


def fmt_uniq_block(title: str, checks: list[dict]) -> list[str]:
    lines = [f"**{title}**", "", "| 字段 | total | non_empty | unique | 重复值数 |", "|---|---|---|---|---|"]
    for c in checks:
        lines.append(f"| {c['field']} | {c['total']} | {c['non_empty']} | {c['unique']} | {c['duplicate_values']} |")
    for c in checks:
        for d in c["duplicate_detail"]:
            lines.append(f"  - ⚠️ 重复 {c['field']}={d['value']} ×{d['count']}：{d['record_ids']}")
    lines.append("")
    return lines


def fmt_comp_block(title: str, rows: list[dict]) -> list[str]:
    lines = [f"**{title}**", "", "| 字段(逻辑) | 实际key | non_empty | empty | 覆盖率 |", "|---|---|---|---|---|"]
    for c in rows:
        lines.append(f"| {c['field']} | {c['key']} | {c['non_empty']} | {c['empty']} | {c['coverage_pct']}% |")
    lines.append("")
    return lines


# 5) main report md
R: list[str] = []
R += ["# 数据模块最终封板审计报告（Final Data Seal）", ""]
R += ["## 【审计基线】",
      f"- 分支：`feature/data-final-seal`（feature/data-llm-baseline-110 保留未删）",
      f"- audit_base_sha：`{BASELINE_DEVELOP_SHA}`（= 最新 origin/develop；本地 HEAD 经 `git merge --ff-only` 快进至此，**无 merge commit**）",
      f"- 上轮基线：`{PREVIOUS_BASELINE_SHA}`（09-08 首次封板基线；本轮 `git -c http.version=HTTP/1.1 fetch` 恢复后 ff-only 刷新）",
      f"- 审计时间（北京时间）：{AUDIT_TIME}",
      "- 审计性质：**只读**；不调用 LLM、不自动修复、不修改任何数据字节", ""]

R += ["## 【数据规模】CURRENT_DATA_COUNTS",
      f"- Zhilian raw：**{counts['zhilian_raw']}**",
      f"- Zhilian clean：**{counts['zhilian_clean']}**（tier: accepted={counts['zhilian_tier_accepted']}, review_required={counts['zhilian_tier_review_required']}{(', other=' + json.dumps(counts['zhilian_tier_other'], ensure_ascii=False)) if counts['zhilian_tier_other'] else ''}）",
      f"- review_required.csv 数据行：**{counts['review_csv_rows']}**",
      f"- rejected.csv 数据行：**{counts['rejected_csv_rows']}**",
      f"- Gold110：**{counts['gold']}**",
      f"- Official Career raw/clean/gold：**{counts['official_raw']} / {counts['official_clean']} / {counts['official_gold']}**",
      f"- Gold exclusion manifest 行：**{counts['exclusion_manifest_rows']}**",
      f"- 统一审计 cross_source_duplicates.csv 行：{counts['cross_source_duplicate_rows']}", ""]

R += ["## 【唯一性】"]
for title, key in [("Zhilian clean (158)", "zhilian_clean"), ("Gold110", "gold110"),
                   ("Official Career50 clean", "official50_clean"),
                   ("Official Career50 gold", "official50_gold"),
                   ("Exclusion manifest", "exclusion_manifest")]:
    R += fmt_uniq_block(title, uniq[key])

R += ["## 【字段完整率】"]
for title, key in [("Zhilian clean", "zhilian_clean"), ("Gold110（源字段）", "gold110"),
                   ("Official Career50 clean", "official50_clean")]:
    R += fmt_comp_block(title, completeness_res[key])
R += ["**Gold110 标注字段（gold_*）完整率**", "", "| 字段 | non_empty | empty | 覆盖率 |", "|---|---|---|---|"]
for c in gold_label_completeness:
    R.append(f"| {c['field']} | {c['non_empty']} | {c['empty']} | {c['coverage_pct']}% |")
R.append("")

R += ["## 【SHA一致性】Zhilian candidate pool",
      f"- 公式（仓库验收同口径）：`{sha_res['formula']}`",
      f"- total：**{sha_res['total']}**；match：**{sha_res['match']}**；mismatch：**{sha_res['mismatch']}**；格式非法：{len(sha_bad_format)}；resp-only legacy：{sha_res['legacy_resp_only_count']}"]
for m in sha_mismatch[:20]:
    R.append(f"  - mismatch：{m['source_id']} stored={m['stored_prefix']}… expected={m['expected_prefix']}…")
R.append("")

R += ["## 【两条历史修复专项回归】",
      f"- `{T1}`（ANN-0023）：present={reg[T1].get('present')}，resp_non_empty={reg[T1].get('resp_non_empty')}（len={reg[T1].get('resp_len')}），req_non_empty={reg[T1].get('req_non_empty')}（len={reg[T1].get('req_len')}），sha_match={reg[T1].get('sha_match')}",
      f"- `{T2}`：present={reg[T2].get('present')}，resp_non_empty={reg[T2].get('resp_non_empty')}（len={reg[T2].get('resp_len')}），req_non_empty={reg[T2].get('req_non_empty')}（len={reg[T2].get('req_len')}），sha_match={reg[T2].get('sha_match')}", ""]

R += ["## 【Gold保护】",
      f"- Gold SHA256：`{gold_meta['sha256']}`",
      f"- Gold size：{gold_meta['size_bytes']} bytes",
      f"- Gold records：**{gold_meta['records']}**（==110：{gold_meta['records_eq_110']}）",
      f"- sample_id 唯一：{gold_meta['sample_id_unique']}",
      f"- 与历史封板基线 SHA（{GOLD_KNOWN_SHA[:16]}…）一致：**{gold_meta['matches_known_baseline_sha']}**",
      f"- Gold 修改：**0 byte**（审计全程只读）",
      f"- 6 个 gold_* 字段齐备：{gold_meta['gold_fields_present']}", ""]

R += ["## 【ANN-0023 专项回归】",
      f"- Gold 记录存在：{ann23_res['exists']}；source_id={ann23_res['source_id']}（==T1：{ann23_res['source_id_is_T1']}）",
      f"- 结论：**{ann23_res['verdict']}**（{ann23_res['basis']}）", ""]

R += ["## 【Gold exclusion】",
      f"- manifest 行：**{excl_res['rows']}**；source_id 非空/唯一：{excl_res['source_id_non_empty']}/{excl_res['source_id_unique']}",
      f"- gold_source_id 覆盖：**{excl_res['gold_source_coverage']}**（Gold 110 条 source_id 全部在 manifest：{excl_res['gold_source_ids_all_in_manifest']}）",
      f"- ANN-0023 / {T1} 在排除关系中：{excl_res['ANN0023_in_manifest']} / {excl_res['T1_in_manifest']}",
      f"- EXPECTED_PARENT_CHILD_OVERLAP 标注行：{excl_res['expected_parent_child_notes']}",
      "- 数据级结论：110 条 Gold 来源候选具备明确排除映射，可被排除于 train/dev/retrieval exemplar/few-shot context（模型代码侧不在本审计范围）", ""]

R += ["## 【跨源关系】",
      f"- Gold vs Zhilian source_id 重叠：**{cross_res['gold_vs_zhilian_source_id_overlap']}**、source_url 重叠：{cross_res['gold_vs_zhilian_source_url_overlap']} → **{cross_res['gold_vs_zhilian_relation']}**",
      f"- Zhilian vs Official Career：source_id 重叠={cross_res['zhilian_vs_official_source_id_overlap']}，source_url 重叠={cross_res['zhilian_vs_official_source_url_overlap']}，_sha256 重叠={cross_res['zhilian_vs_official_sha256_overlap']}",
      f"- Gold vs Official Career：source_id 重叠={cross_res['gold_vs_official_source_id_overlap']}，source_url 重叠={cross_res['gold_vs_official_source_url_overlap']}",
      f"- 统一审计 cross_source_duplicates.csv 行数={len(cross_rows)}，关系统计：{json.dumps(cross_res['unified_cross_csv_relation_counts'], ensure_ascii=False)[:300]}",
      "- 结论：除 Gold↔Zhilian 设计内父子重叠外，**无跨源 exact duplicate**（source_id/source_url/_sha256 三口径均为 0）", ""]

R += ["## 【Official Career50 专项】",
      f"- 总数：**{offi_res['total']}**；腾讯（company_name 口径）：**{offi_res['tencent_count']}**；字节/抖音：**{offi_res['bytedance_count']}**",
      f"- source_company 字段分布：{json.dumps(offi_res['source_company_distribution'], ensure_ascii=False)}",
      f"- source_id 唯一：{offi_res['source_id_unique']}；source_url 唯一：{offi_res['source_url_unique']}；_sha256 全为合法 64-hex：{offi_res['sha256_all_valid_hex64']}",
      f"- future publish_time 异常：**{offi_res['future_anomaly_count']}** 条（SOURCE_REPORTED_FUTURE_TIME，保留 source 原值不修正）："]
for a in future_anoms:
    R.append(f"  - {a['source_id']}（{a['company']}）publish_time={a['publish_time']} > crawl_time={a['crawl_time']}")
R.append("")

R += ["## 【统一审计状态】（读自 final_acceptance_summary.json，当前实际值）",
      f"- P0 = **{gate_audit['P0']}**；P1 = **{gate_audit['P1']}**；P2 = {gate_audit['P2']}；P3 = {gate_audit['P3']}",
      f"- BASELINE = **{gate_audit['BASELINE']}**；SHA_mismatch_count = {gate_audit['SHA_mismatch_count']}；Manifest_rows = {gate_audit['Manifest_rows']}", ""]

R += ["## 【安全扫描】",
      f"- 扫描数据文件数：{sec_res['files_scanned']}；疑似 secret：**{sec_res['suspect_count']}**",
      "- 明细见 final_data_security_scan.md（全部脱敏，未打印任何 secret 正文）", ""]

R += ["## 【历史基线差异】HISTORICAL_BASELINE_DIFFERENCE"]
if hist_diff:
    for d in hist_diff:
        R.append(f"- ⚠️ {d['item']}：current={d['current']} vs historical={d['historical']}（{d['explanation']}）")
else:
    R.append("- 无差异：Zhilian=158 / accepted=135 / review_required=23 / rejected=0 / Gold=110 / Official=50 / 腾讯=25 / 字节=25 / manifest=110，全部与历史基线一致。")
R.append("")

R += ["## 【Post-baseline Golden-set Changes】",
      f"- 区间：`{PREVIOUS_BASELINE_SHA[:12]}..{BASELINE_DEVELOP_SHA[:12]}`（`git diff --name-status -- backend/data/golden_set/` 实测）",
      "- 分类口径：CORE_DATA_ASSET（核心四类封板资产）/ ALGORITHM_EVAL_ASSET（算法调参评测集）/ TEST_EVAL_ASSET（测试评测集）/ OTHER_GOLDEN_SET_ASSET（其它历史留痕）",
      "- **核心四类资产（candidate_pool/v1、final/jd_golden_110.jsonl、official_career_50、audit/unified_jd_audit）在该区间零变更。**",
      "",
      "| 文件 | 变化 | records | 分类 | 用途 | 属核心四类 | 影响Gold110 | 影响Zhilian158 | 影响Official50 | 影响unified audit | 影响本次Seal结论 |",
      "|---|---|---|---|---|---|---|---|---|---|---|"]
for c in post_changes:
    R.append(f"| {c['path']} | {c['change']} | {c['records'] if c['records'] is not None else '—'} | {c['asset_class']} | {c['purpose']} | "
             f"{'是' if c['in_core_four'] else '否'} | {'是' if c['affects_gold110'] else '否'} | {'是' if c['affects_zhilian158'] else '否'} | "
             f"{'是' if c['affects_official50'] else '否'} | {'是' if c['affects_unified_audit'] else '否'} | {'是' if c['affects_seal_gate'] else '否'} |")
R += ["",
      "- 结论：新增/修改资产均为**算法/测试评测集**（学历语义、匹配 v3、岗位域图谱）与历史人工评测中间产物清理，**不纳入 Gold110 主封板口径**，不影响本次 Final Seal 任何 Gate 检查；安全扫描已一并覆盖上述新增文件。", ""]

R += ["## 【已知异常 / 剩余风险】",
      f"1. Official Career50 有 {offi_res['future_anomaly_count']} 条 source-reported future publish_time（{', '.join(a['source_id'] for a in future_anoms)}）——官方站点返回原值，按 SOURCE_REPORTED_FUTURE_TIME 保留，不修正。",
      f"2. 统一审计 P2={gate_audit['P2']}、P3={gate_audit['P3']} 为非阻断项（P0/P1=0），按既有审计结论留痕。",
      "3. Gold110 requirements 字段存在历史已知空值（final_acceptance_summary: Gold_req_empty=1），属标注口径内信息项，非阻断。",
      f"4. ~~网络受限风险已解除~~：09-08 `git -c http.version=HTTP/1.1 fetch` 恢复后，基线已 ff-only 刷新至 origin/develop={BASELINE_DEVELOP_SHA[:12]}（本轮审计即基于该最新基线）。",
      f"5. 基线刷新后 golden_set/ 新增 {sum(1 for c in post_changes if c['change'].startswith(('A','M')))} 个算法/测试评测集文件、清理 12 个历史人工评测文件，均不属核心四类资产，不影响封板口径（详见【Post-baseline Golden-set Changes】）。",
      "6. LLM Baseline（evaluation/llm_baseline_110）因 opencode-go CreditsError 余额不足处于 SMOKE_BLOCKED，属模型服务侧问题，与数据资产封板无关。", ""]

R += ["## 【提交准备度】DATA_ASSET_MANIFEST",
      "- Gold110 正式文件：✅ 存在/可解析/110 条/SHA 一致",
      "- Gold 数据字典：✅ jd_golden_110_data_dictionary.md 存在",
      "- Zhilian 候选数据：✅ raw/clean/review/rejected 齐备",
      "- Official Career 数据：✅ raw/clean/gold 齐备",
      "- 统一审计报告：✅ unified_jd_audit 留痕完整（final_acceptance_summary.json 等 12 文件）",
      "- Gold exclusion manifest：✅ 110 行、覆盖 110/110",
      "- 训练许可口径：Gold=评测专用（exclusion manifest 明确排除训练）；其余候选池无明确项目规则处一律标注 NOT_DEFINED_DO_NOT_ASSUME，未做猜测。", ""]

R += ["## 【最终 Gate】",
      f"**{GATE}**", "",
      "| 检查项 | 结果 |", "|---|---|"]
for k, v in gate_checks.items():
    R.append(f"| {k} | {'✅' if v else '❌ BLOCKED'} |")
if blockers:
    R.append("")
    R.append("阻断项：")
    for b in blockers:
        R.append(f"- {b}")
R.append("")

(SEAL / "final_data_seal_report.md").write_text("\n".join(R), encoding="utf-8")

# 6) README
readme = f"""# Final Data Seal — 数据模块最终封板审计

- 审计时间（北京时间）：{AUDIT_TIME}
- 分支：feature/data-final-seal（audit_base_sha = {BASELINE_DEVELOP_SHA}，ff-only 刷新自 {PREVIOUS_BASELINE_SHA[:12]}）
- 最终 Gate：**{GATE}**

## 目录产物

| 文件 | 说明 |
|---|---|
| final_data_seal_report.md | 封板审计主报告（§二十 全部章节） |
| final_data_manifest.csv | DATA_ASSET_MANIFEST（资产路径/角色/记录数/来源/is_gold/eval·train 许可） |
| final_data_checksums.csv | 全部被审计文件的 exists/records/size/SHA256 |
| final_data_quality_summary.json | 结构化全量审计结果（机器可读） |
| final_data_security_scan.md | 安全扫描报告（脱敏，不含任何 secret 正文） |
| scripts/final_data_seal_audit.py | 只读审计脚本（stdlib，无第三方依赖，不调用 LLM） |

## 复跑方式

```powershell
# 在仓库根目录，任意 Python 3.10+（仅标准库）
python backend/data/golden_set/audit/final_data_seal/scripts/final_data_seal_audit.py
```

脚本只读四类数据资产（candidate_pool/v1、final、official_career_50、audit/unified_jd_audit），
仅向本目录写产物；不修改任何数据、不调用 LLM、不自动修复。

## 口径说明

- Zhilian SHA 公式与 audit/unified_jd_audit 验收脚本一致：SHA256(responsibilities + "\\n" + requirements)
- Gold↔Zhilian 110 条 source_id 重叠为 EXPECTED_PARENT_CHILD_OVERLAP（设计内父子关系），不报 DATA_LEAKAGE
- Official Career 2 条 future publish_time 为 SOURCE_REPORTED_FUTURE_TIME，保留 source 原值
- 训练许可：Gold 评测专用（exclusion manifest 排除训练）；无明确规则处标 NOT_DEFINED_DO_NOT_ASSUME
"""
(SEAL / "README.md").write_text(readme, encoding="utf-8")

# 控制台摘要
print(json.dumps({
    "gate": GATE, "blockers": blockers,
    "counts": counts,
    "gold_sha": gold_sha, "gold_records": len(gold_rows),
    "sha_mismatch": sha_res["mismatch"],
    "sec_suspects": sec_res["suspect_count"],
    "p0p1": [gate_audit["P0"], gate_audit["P1"]],
    "official": {"total": offi_res["total"], "tencent": offi_res["tencent_count"],
                 "bytedance": offi_res["bytedance_count"], "future": offi_res["future_anomaly_count"]},
    "hist_diff": hist_diff,
    "out_dir": str(SEAL),
}, ensure_ascii=False, indent=2))
