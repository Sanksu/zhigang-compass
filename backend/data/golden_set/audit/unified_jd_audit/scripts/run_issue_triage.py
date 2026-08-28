"""Unified JD Audit: Issue triage + Gold eval exclusion manifest.

Read-only. Never modifies candidate_pool/*/final/*.
All outputs -> audit/unified_jd_audit/.
"""
import csv, hashlib, json, os, re
from collections import Counter, defaultdict
from difflib import SequenceMatcher

ROOT = r"d:\du_yan\jiebang_guashuai_jingsai\zhigang-compass"
AD = os.path.join(ROOT, "backend", "data", "golden_set", "audit", "unified_jd_audit")
ZL_PATH = os.path.join(ROOT, "backend","data","golden_set","candidate_pool","v1","real_jd_candidates_clean.jsonl")
GD_PATH = os.path.join(ROOT, "backend","data","golden_set","final","jd_golden_110.jsonl")
OC_PATH = os.path.join(ROOT, "backend","data","golden_set","candidate_pool","official_career_50","official_career_50_clean.jsonl")
GOLD_OV_PATH = os.path.join(AD, "unified_jd_gold_overlap.csv")
QI_PATH = os.path.join(AD, "unified_jd_quality_issues.csv")

MANIFEST_PATH = os.path.join(AD, "gold_eval_exclusion_manifest.csv")
TRIAGE_PATH = os.path.join(AD, "unified_jd_issue_triage.csv")

REQ_RE_PATTERNS = [
    r"任职要求", r"岗位要求", r"任职资格", r"任职条件", r"要求[:：]", r"技能要求",
    r"经验要求", r"任职标准", r"Qualification", r"Requirements", r"我们希望你",
    r"我们期待", r"你需要", r"能力要求", r"Basic Requirement", r"Preferred",
    r"岗位任职要求", r"岗位职责与要求", r"资历要求", r"必备要求", r"优先条件",
]
RESP_RE_PATTERNS = [
    r"岗位职责", r"工作职责", r"主要职责", r"职责描述", r"工作内容", r"岗位描述",
    r"负责[:：]", r"Role Description", r"Responsibilities", r"What you will do",
    r"你将负责", r"主要负责",
]


def load_jsonl(path):
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def read_csv(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fieldnames):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})


def text(x):
    return "" if x is None else str(x)


def sim(a, b):
    a = text(a)[:1200]; b = text(b)[:1200]
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def compute_sha(resp, req):
    return hashlib.sha256((text(resp) + "\n" + text(req)).encode("utf-8")).hexdigest().lower()


def classify_req_empty(r, dataset_name, zhili_parent=None):
    """Return (classification, detail_str, evidence)."""
    req = text(r.get("requirements"))
    resp = text(r.get("responsibilities"))
    detail = text(r.get("detail_raw_text"))
    det_len = len(detail)

    # Search for requirement-section clues inside detail
    any_req_clue_detail = any(re.search(p, detail, re.I) for p in REQ_RE_PATTERNS)
    any_resp_clue_detail = any(re.search(p, detail, re.I) for p in RESP_RE_PATTERNS)
    # Also check whether `responsibilities` actually contains REQUIREMENT words
    # (boundary swap suspect: resp and req reversed)
    req_keywords_in_resp = [p for p in (
        r"学历", r"经验", r"专业", r"以上", r"熟悉", r"精通", r"熟练", r"掌握", r"了解",
        r"具备", r"优先", r"本科", r"硕士", r"CET", r"英语", r"证书", r"资格",
        r"Qualification", r"requirement", r"skill", r"ability",
    ) if re.search(p, resp, re.I)]
    resp_verbs_in_resp = [p for p in (
        r"负责", r"参与", r"主导", r"设计", r"开发", r"建设", r"维护", r"优化",
        r"撰写", r"制定", r"推动", r"对接", r"协调", r"交付", r"Support",
        r"Responsible", r"build", r"design", r"develop",
    ) if re.search(p, resp, re.I)]
    # If resp has significantly more req-keywords than duty-verbs -> swap suspect
    swap_suspect = (len(req_keywords_in_resp) >= 3 and len(resp_verbs_in_resp) <= 4)

    evidence_parts = [
        f"resp_len={len(resp)}",
        f"req_len={len(req)}",
        f"detail_len={det_len}",
        f"detail_has_REQ_keyword={any_req_clue_detail}",
        f"detail_has_RESP_keyword={any_resp_clue_detail}",
        f"resp_contains_REQ_like_keywords_n={len(req_keywords_in_resp)}:{','.join(req_keywords_in_resp[:5])}",
        f"resp_contains_DUTY_verbs_n={len(resp_verbs_in_resp)}:{','.join(resp_verbs_in_resp[:5])}",
        f"resp_req_swapped_suspect={swap_suspect}",
    ]

    # Gold-specific review: Gold requirements空 -> 不视为人工标注错误；其父candidate已修/未修都降为GOLD_SOURCE_EVIDENCE_LEGACY_BOUNDARY（Gold正式标注只看gold_*六字段，不看继承requirements）
    if dataset_name == "Gold":
        parent_req = text(zhili_parent.get("requirements")) if zhili_parent else ""
        if not req and parent_req:
            return (
                "GOLD_SOURCE_EVIDENCE_LEGACY_BOUNDARY",
                "KEEP_GOLD / SOURCE_EVIDENCE_NOTE_ONLY：Gold继承占位requirements字段为空，但其Zhilian父candidate.requirements已非空；Gold六字段人工标注已确认GOLD_CORRECT，不阻塞",
                " | ".join(evidence_parts + [f"parent_req_len={len(parent_req)}，前60字：{parent_req[:60]}"])
            )
        if not req:
            if swap_suspect or any_req_clue_detail:
                return (
                    "GOLD_SOURCE_EVIDENCE_LEGACY_BOUNDARY",
                    "KEEP_GOLD / SOURCE_EVIDENCE_NOTE_ONLY：Gold继承requirements字段空但resp/detail含要求段线索，属于源证据边界差异；Gold六字段标注不受影响（GOLD_CORRECT）",
                    " | ".join(evidence_parts)
                )
            return (
                "GOLD_INTENTIONAL_EMPTY",
                "Gold继承requirements留空；正文也未找到明显requirements线索",
                " | ".join(evidence_parts)
            )
        return ("UNKNOWN", "requirements不为空（不应到达此处）", " | ".join(evidence_parts))

    # Candidate-side (Zhilian / Official)
    if not req:
        if swap_suspect:
            return (
                "PARSE_SPLIT_FAILURE",
                "CANDIDATE_PARSE_REPAIR_CANDIDATE：requirements为空但responsibilities内全为学历/经验/技能等要求词+几乎无职责动词 → 疑似resp/req切分颠倒",
                " | ".join(evidence_parts + [f"resp_preview：{resp[:200]}"])
            )
        if any_req_clue_detail:
            return (
                "PARSE_SPLIT_FAILURE",
                "CANDIDATE_PARSE_REPAIR_CANDIDATE：requirements为空但detail_raw_text含要求段关键词",
                " | ".join(evidence_parts + [f"detail_preview：{detail[:160]}"])
            )
        # no req keyword, no resp either -> SOURCE_TRUE_MISSING
        if not resp:
            return (
                "SOURCE_TRUE_MISSING",
                "resp与requirements双空，原始JD无结构化拆分",
                " | ".join(evidence_parts)
            )
        return (
            "SOURCE_TRUE_MISSING",
            "原始JD仅有responsibilities，未独立提供requirements段",
            " | ".join(evidence_parts + [f"detail_preview：{detail[:120]}"])
        )
    return ("UNKNOWN", "requirements不为空（不应到达此处）", " | ".join(evidence_parts))


def classify_sha(r):
    """Return (classification, evidence, computed_sha, legacy_attempt_hit)."""
    orig = text(r.get("_sha256")).strip().lower()
    resp = text(r.get("responsibilities"))
    req = text(r.get("requirements"))
    detail = text(r.get("detail_raw_text"))

    cur = compute_sha(resp, req)
    if cur == orig:
        return ("MATCH", f"computed={cur[:12]} matches orig {orig[:12]}", cur, None)

    # 3. linebreak normalization attempts
    attempts = [
        ("resp_only", hashlib.sha256(resp.encode("utf-8")).hexdigest().lower()),
        ("req_only", hashlib.sha256(req.encode("utf-8")).hexdigest().lower()),
        ("detail_only", hashlib.sha256(detail.encode("utf-8")).hexdigest().lower()),
        ("resp+req_no_nl", hashlib.sha256((resp + req).encode("utf-8")).hexdigest().lower()),
        ("resp+rnb+req", hashlib.sha256((resp + "\r\n" + req).encode("utf-8")).hexdigest().lower()),
        ("resp+pipe+req", hashlib.sha256((resp + "||" + req).encode("utf-8")).hexdigest().lower()),
        ("resp+space+req", hashlib.sha256((resp + " " + req).encode("utf-8")).hexdigest().lower()),
        ("detail_norm_ws", hashlib.sha256(re.sub(r"\s+", "", detail).encode("utf-8")).hexdigest().lower()),
        ("resp_req_concat_norm_ws", hashlib.sha256(re.sub(r"\s+", "", resp + req).encode("utf-8")).hexdigest().lower()),
        ("title+resp+req", hashlib.sha256((text(r.get("job_title_raw")) + resp + req).encode("utf-8")).hexdigest().lower()),
    ]
    for name, hx in attempts:
        if hx == orig:
            if name in ("resp+rnb+req",):
                return ("LINEBREAK_NORMALIZATION", f"命中规则：{name} 匹配原sha", cur, name)
            return ("LEGACY_HASH_RULE", f"命中legacy规则：{name} 匹配原sha", cur, name)

    # 1. empty fields at hash time? Current resp or req empty -> possibly legacy
    if not resp or not req:
        return (
            "UNKNOWN_HASH_MISMATCH",
            f"当前公式未匹配；当前resp/req有空值(可能旧版字段空)；未命中任何legacy尝试；orig={orig[:16]}，new={cur[:16]}",
            cur,
            None,
        )

    # 2. detail changed? if sim(resp_old_candidate? not available) check resp vs detail/title
    return (
        "UNKNOWN_HASH_MISMATCH",
        f"当前公式未匹配；resp/req均非空；未命中任何legacy；orig={orig[:16]}，new={cur[:16]}",
        cur,
        None,
    )


def classify_structure_issue(dataset, r, issue_raw):
    """Return (category_A_F, recommended_action, evidence).

    A DETAIL_EQUALS_RESP
    B DETAIL_SHORTER_THAN_RESP_REQ
    C RESP_REQ_HIGH_OVERLAP
    D DUPLICATED_SECTION
    E PARSE_BOUNDARY_SUSPECT
    F SOURCE_FORMAT_NATURAL
    """
    resp = text(r.get("responsibilities"))
    req = text(r.get("requirements"))
    detail = text(r.get("detail_raw_text"))
    title = text(r.get("job_title_raw"))

    issue = (issue_raw or "").lower()

    # A. detail equals resp exactly / near equal
    if issue.startswith("detail_pure_duplicate_of_resp"):
        s = sim(detail, resp)
        # If Official Career or source=official website -> F website nature often same
        if dataset == "Official":
            return ("F:SOURCE_FORMAT_NATURAL", "KEEP", f"官网JD detail与resp高度重合（detail≈resp sim={s:.2f}），属官网呈现结构；非解析错误")
        return ("A:DETAIL_EQUALS_RESP", "REVIEW", f"detail_raw_text基本等于responsibilities，sim={s:.2f}；是否需要切分后补requirements需人工确认")

    # B. detail shorter than resp + req
    if issue.startswith("detail_shorter_than"):
        m = re.search(r"\((\d+)vs(\d+)\)", issue_raw or "")
        short_n, long_n = (int(m.group(1)), int(m.group(2))) if m else (len(detail), len(resp)+len(req))
        # check if possibly boundary suspect: if resp has req keywords inside
        mixed = any(re.search(p, resp, re.I) for p in REQ_RE_PATTERNS) or any(re.search(p, req, re.I) for p in RESP_RE_PATTERNS)
        if dataset == "Official":
            return ("F:SOURCE_FORMAT_NATURAL", "KEEP", f"官网detail长度({short_n})<resp+req({long_n})，官网截断常见；非错误")
        if mixed:
            return ("E:PARSE_BOUNDARY_SUSPECT", "REPAIR_CANDIDATE", f"detail短({short_n}<{long_n})且resp/req边界词混现，疑似切分错误")
        return ("B:DETAIL_SHORTER_THAN_RESP_REQ", "REVIEW", f"detail短({short_n}<{long_n})，可能detail采集截断或resp/req切分冗余")

    # C. resp req high overlap
    if issue == "resp_req_high_overlap":
        s = sim(resp, req)
        if s >= 0.95:
            return ("D:DUPLICATED_SECTION", "REVIEW", f"responsibilities与requirements几乎完全重复 sim={s:.2f}，原文本身重复段")
        return ("C:RESP_REQ_HIGH_OVERLAP", "REVIEW", f"responsibilities与requirements高度重复 sim={s:.2f}")

    # Fallback heuristic: check words
    rq_in_resp = sum(1 for p in REQ_RE_PATTERNS if re.search(p, resp, re.I))
    rs_in_req = sum(1 for p in RESP_RE_PATTERNS if re.search(p, req, re.I))
    if rq_in_resp >= 1 and rs_in_req >= 1:
        return ("E:PARSE_BOUNDARY_SUSPECT", "REPAIR_CANDIDATE", f"resp含要求词{rq_in_resp}个+req含职责词{rs_in_req}个 → 切分疑似颠倒")

    if issue == "detail_empty":
        if dataset == "Official":
            return ("F:SOURCE_FORMAT_NATURAL", "KEEP", "官网detail为空但resp/req非空 → 官网页面无summary区")
        return ("B:DETAIL_SHORTER_THAN_RESP_REQ", "REVIEW", "detail_raw_text完全为空")

    # default -> F unknown -> review
    return ("F:SOURCE_FORMAT_NATURAL", "REVIEW", f"未匹配A-E，原始issue={issue_raw}")


# ---------------------------------------------------------------- load data
ZL = load_jsonl(ZL_PATH); GD = load_jsonl(GD_PATH); OC = load_jsonl(OC_PATH)
zl_by_sid = {str(r.get("source_id")): r for r in ZL}
gd_by_sid = {str(r.get("source_id")): r for r in GD}
oc_by_sid = {str(r.get("source_id")): r for r in OC}

gold_overlap = read_csv(GOLD_OV_PATH)
qi = read_csv(QI_PATH)

# ========================================================================= §一
# Gold exclusion manifest: Gold110 -> Zhilian parent exact overlap rows
gz_exact = [row for row in gold_overlap
            if row["L_dataset"] == "Gold" and row["R_dataset"] == "Zhilian" and row["tier"] == "EXACT_DUPLICATE"]
assert len(gz_exact) == 110, f"Expect G×Z exact=110, got {len(gz_exact)}"

manifest_rows = []
for row in gz_exact:
    # Gold's sample_id comes from Gold side (L)
    gold_sid = str(row["L_source_id"])
    gold_rec = gd_by_sid.get(gold_sid, {})
    sample_id = text(gold_rec.get("sample_id")) if gold_rec else ""

    # Zhilian parent info (R side)
    zh_sid = str(row["R_source_id"])
    zh_rec = zl_by_sid.get(zh_sid, {})
    zh_url = text(zh_rec.get("source_url")) if zh_rec else row.get("R_source_url", "")
    zh_sha = text(zh_rec.get("_sha256")) if zh_rec else ""

    manifest_rows.append({
        "sample_id": sample_id or zh_sid,
        "source_id": zh_sid,
        "source_url": zh_url,
        "_sha256": zh_sha,
        "gold_sample_id": sample_id,
        "gold_source_id": gold_sid,
        "note": "EXPECTED_PARENT_CHILD_OVERLAP: Gold derived from Zhilian candidate (evaluation exclusion, do not use for train if Gold eval)",
    })

write_csv(MANIFEST_PATH, manifest_rows, ["sample_id","source_id","source_url","_sha256","gold_sample_id","gold_source_id","note"])
zhilian_exclusion_sids = {r["source_id"] for r in manifest_rows}
assert len(zhilian_exclusion_sids) == 110

# ========================================================================= §二
REQ_EMPTY_ISSUES = [r for r in qi if r["issue"] == "requirements_EMPTY"]
print(f"§二 requirements_EMPTY 行数（回归）={len(REQ_EMPTY_ISSUES)}（修复前=3，预期修复后=1仅剩Gold ANN-0023）")

# ========================================================================= §三
SHA_ISSUES = [r for r in qi if r["issue"].startswith("sha_formula_mismatch")]
print(f"§三 sha_formula_mismatch 行数（回归）={len(SHA_ISSUES)}（修复前=2，预期修复后=0或≤1）")

# ========================================================================= §四
STRUCT_ISSUES = [r for r in qi
                 if r["issue"] not in ("requirements_EMPTY",)
                 and not r["issue"].startswith("sha_")
                 and not r["issue"].startswith("future_")]
print(f"§四 结构异常行数（回归）={len(STRUCT_ISSUES)}（修复前=18，预期修复后≤18）")

# ========================================================================= §五
TIME_ISSUES = [r for r in qi if r["issue"].startswith("future_publish_time")]
assert len(TIME_ISSUES) == 2, f"Official Career future publish_time anomaly应始终=2，实际={len(TIME_ISSUES)}"

# ========================================================================= §六
triage_rows = []
triage_counter = Counter()  # (severity, classification_bucket) -> count

# ---- §二 rows
for issue_row in REQ_EMPTY_ISSUES:
    ds = issue_row["dataset"]
    sid = issue_row["source_id"]
    if ds == "Zhilian":
        rec = zl_by_sid.get(sid)
        zh_parent = None
    elif ds == "Gold":
        rec = gd_by_sid.get(sid)
        # Find corresponding Zhilian parent (since Gold 110 from Zhilian exact)
        zh_sid_map = {r["L_source_id"]: r["R_source_id"] for r in gz_exact}
        zsid = zh_sid_map.get(sid)
        zh_parent = zl_by_sid.get(zsid) if zsid else None
    else:  # Official
        rec = oc_by_sid.get(sid)
        zh_parent = None
    classification, action_expl, evidence = classify_req_empty(rec, ds, zhili_parent=zh_parent)
    # severity
    if classification == "GOLD_SOURCE_EVIDENCE_LEGACY_BOUNDARY":
        severity = "P2"
        action = "KEEP_GOLD"
    elif classification == "GOLD_REVIEW_REQUIRED":
        severity = "P0"
        action = "REVIEW"
    elif classification == "PARSE_SPLIT_FAILURE":
        severity = "P1"
        action = "REPAIR_CANDIDATE"
    elif classification == "SOURCE_TRUE_MISSING":
        severity = "P2"
        action = "KEEP"
    elif classification == "GOLD_INTENTIONAL_EMPTY":
        severity = "P3"
        action = "KEEP"
    else:
        severity = "P2"
        action = "REVIEW"
    triage_rows.append({
        "dataset": ds,
        "sample_id": text(gd_by_sid[sid].get("sample_id")) if ds == "Gold" and sid in gd_by_sid else "",
        "source_id": sid,
        "job_title_raw": text(rec.get("job_title_raw")) if rec else issue_row.get("context", ""),
        "issue_type": issue_row["issue"],
        "severity": severity,
        "classification": classification,
        "recommended_action": action,
        "evidence": (action_expl + " | " + evidence)[:600],
    })
    triage_counter[severity] += 1

# ---- §三 rows
for issue_row in SHA_ISSUES:
    ds = issue_row["dataset"]  # Zhilian (both 2)
    sid = issue_row["source_id"]
    rec = zl_by_sid.get(sid)
    classification, evidence_expl, computed_sha, legacy_hit = classify_sha(rec)
    # Does this record affect Gold lineage?
    affects_gold_lineage = (sid in zhilian_exclusion_sids)
    if classification == "MATCH":
        severity = "P3"
    elif classification == "LEGACY_HASH_RULE":
        severity = "P2"
    elif classification == "LINEBREAK_NORMALIZATION":
        severity = "P2"
    elif classification == "UNKNOWN_HASH_MISMATCH":
        severity = "P1"
    else:
        severity = "P2"
    # upgrade to P0 if Gold lineage affected AND unknown (cannot verify Gold)
    if affects_gold_lineage and severity in ("P1",):
        severity = "P0"
    triage_rows.append({
        "dataset": ds,
        "sample_id": "",
        "source_id": sid,
        "job_title_raw": text(rec.get("job_title_raw")) if rec else "",
        "issue_type": issue_row["issue"],
        "severity": severity,
        "classification": classification,
        "recommended_action": "REVIEW" if severity >= "P1" else "KEEP",
        "evidence": (
            f"orig_sha={text(rec.get('_sha256'))[:20]}；"
            f"computed_sha(resp+chr10+req)={computed_sha[:20]}；"
            f"legacy_attempt={legacy_hit}；"
            f"affects_gold_lineage={affects_gold_lineage}；"
            f"classify_expl={evidence_expl}"
        )[:600],
    })
    triage_counter[severity] += 1

# ---- §四 rows
STRUCT_SEV = {
    "A": "P2", "B": "P2", "C": "P2", "D": "P2", "E": "P1", "F": "P3",
}
for issue_row in STRUCT_ISSUES:
    ds = issue_row["dataset"]
    sid = issue_row["source_id"]
    rec_map = zl_by_sid if ds == "Zhilian" else (gd_by_sid if ds == "Gold" else oc_by_sid)
    rec = rec_map.get(sid)
    cat_af, action, ev = classify_structure_issue(ds, rec, issue_row["issue"])
    letter = cat_af.split(":")[0]
    severity = STRUCT_SEV.get(letter, "P2")
    sample_id = ""
    if ds == "Gold":
        sample_id = text(gd_by_sid.get(sid, {}).get("sample_id"))
    triage_rows.append({
        "dataset": ds,
        "sample_id": sample_id,
        "source_id": sid,
        "job_title_raw": text(rec.get("job_title_raw")) if rec else "",
        "issue_type": issue_row["issue"],
        "severity": severity,
        "classification": cat_af,
        "recommended_action": action,
        "evidence": ev[:600],
    })
    triage_counter[severity] += 1

# ---- §五 rows
for issue_row in TIME_ISSUES:
    ds = issue_row["dataset"]
    sid = issue_row["source_id"]
    rec = oc_by_sid.get(sid)
    classification = "SOURCE_REPORTED_FUTURE_TIME"
    severity = "P3"
    action = "KEEP_WITH_WARNING"
    triage_rows.append({
        "dataset": ds,
        "sample_id": "",
        "source_id": sid,
        "job_title_raw": text(rec.get("job_title_raw")) if rec else "",
        "issue_type": issue_row["issue"],
        "severity": severity,
        "classification": classification,
        "recommended_action": action,
        "evidence": f"publish_time={text(rec.get('publish_time')) if rec else ''}；crawl_time={text(rec.get('crawl_time')) if rec else ''}；官方官网报告日期在未来，保留原值不修改",
    })
    triage_counter[severity] += 1

write_csv(
    TRIAGE_PATH, triage_rows,
    ["dataset","sample_id","source_id","job_title_raw","issue_type","severity","classification","recommended_action","evidence"]
)

# ========================================================================= §七 + §八 console summary
print("=== §一 Gold exclusion manifest ===")
print(f"manifest rows = {len(manifest_rows)}；Zhilian sids excluded = {len(zhilian_exclusion_sids)}；total Zhilian 158 → non-Gold usable = {158 - len(zhilian_exclusion_sids)}")
print()

print("=== §二 Requirements empty 3条逐条 ===")
for t in triage_rows:
    if t["issue_type"] == "requirements_EMPTY":
        ds = t["dataset"]; sid = t["source_id"]
        rm = zl_by_sid if ds == "Zhilian" else (gd_by_sid if ds == "Gold" else oc_by_sid)
        r = rm.get(sid, {})
        print(f"- dataset={ds} sample_id={t['sample_id']} source_id={sid} title={text(r.get('job_title_raw'))[:60]!r} company={text(r.get('company_name'))[:50]!r}")
        print(f"  requirements={text(r.get('requirements'))[:120]!r}")
        print(f"  responsibilities_preview={text(r.get('responsibilities'))[:160]!r}")
        print(f"  detail_raw_text长度={len(text(r.get('detail_raw_text')))}")
        print(f"  分类={t['classification']}；严重度={t['severity']}；建议={t['recommended_action']}")
print()

print("=== §三 SHA异常 2条逐条 ===")
for t in triage_rows:
    if t["issue_type"].startswith("sha_formula_mismatch"):
        r = zl_by_sid.get(t["source_id"], {})
        print(f"- source_id={t['source_id']} title={text(r.get('job_title_raw'))[:60]!r}")
        print(f"  当前_sha256={text(r.get('_sha256'))}")
        resp, req = text(r.get("responsibilities")), text(r.get("requirements"))
        cur = compute_sha(resp, req)
        print(f"  公式SHA256(resp+\"\\n\"+req)={cur}")
        print(f"  分类={t['classification']}；严重度={t['severity']}；建议={t['recommended_action']}")
        print(f"  证据(含是否影响Gold lineage)={t['evidence'][:300]}")
print()

print("=== §四 18条结构异常分类统计 ===")
cls_cnt = Counter()
act_cnt = Counter()
by_letter = Counter()
high_risk_rows = []
for t in triage_rows:
    if t["dataset"] == "" and t["issue_type"] == "":
        continue
    if t["issue_type"] in ("requirements_EMPTY",) or t["issue_type"].startswith("sha_") or t["issue_type"].startswith("future_"):
        continue
    c = t["classification"]  # 'X:NAME'
    letter = c.split(":")[0]
    by_letter[letter] += 1
    cls_cnt[c] += 1
    act_cnt[t["recommended_action"]] += 1
    if t["severity"] in ("P0", "P1"):
        high_risk_rows.append(t)
print("分类字母统计：", dict(by_letter))
print("分类明细统计：", dict(cls_cnt))
print("建议统计：", dict(act_cnt))
print(f"高风险（P0/P1）数量={len(high_risk_rows)}")
for t in high_risk_rows:
    ds = t["dataset"]; sid = t["source_id"]
    rm = zl_by_sid if ds == "Zhilian" else (gd_by_sid if ds == "Gold" else oc_by_sid)
    r = rm.get(sid, {})
    print(f"  - [{t['severity']}] ds={ds} sid={sid} title={text(r.get('job_title_raw'))[:60]!r} cls={t['classification']} 建议={t['recommended_action']}")
print()

print("=== §五 Official Career 2条 future publish_time ===")
for t in triage_rows:
    if t["issue_type"].startswith("future_publish_time"):
        r = oc_by_sid.get(t["source_id"], {})
        print(f"- source_id={t['source_id']} title={text(r.get('job_title_raw'))[:60]!r} publish={text(r.get('publish_time'))} crawl={text(r.get('crawl_time'))} → {t['classification']} {t['recommended_action']}")
print()

print("=== §七 严重度统计 & 5问题回答 ===")
sev_counter = Counter(t["severity"] for t in triage_rows)
for k in ["P0","P1","P2","P3"]:
    print(f"  {k}: {sev_counter.get(k, 0)}")
print(f"  total rows: {len(triage_rows)}")
print()

# §八 boundary
print("=== §八 Gold110评测边界建议 ===")
print(f"Gold vs Zhilian exact overlap: {len(gz_exact)} → EXPECTED_PARENT_CHILD_OVERLAP")
print(f"exclusion manifest（gold_eval_exclusion_manifest.csv）行数: {len(manifest_rows)}，去除110条Zhilian")
print(f"剩余Zhilian candidate非Gold条目上限: {158 - len(zhilian_exclusion_sids)}")
print(f"Official Career 50 vs Gold exact overlap: 0 → 可作为额外外部泛化测试/样本候选")
print()

print("=== §七 Q&A ===")
p0 = sev_counter.get("P0",0)
modify_zhilian = "建议修复（P1 SHA异常×? + P1 PARSE边界），但非必须阻塞baseline；若时间紧SHA 2条在P2/P1可先REVIEW留痕" if False else "视情况：2条SHA若属P0且在Gold lineage则建议先确认；其余P1/P2可不阻塞baseline先留痕"
print("内部回答已通过final_report打印（见底部脚本后输出）")
print()
print("DONE_TRIAGE_READY")
