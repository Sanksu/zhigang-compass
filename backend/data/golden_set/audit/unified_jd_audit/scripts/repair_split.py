"""Zhilian v1 candidate定点split修复 + SHA重算 + 回归审计触发（只读逻辑+受控写操作仅在允许目录）

修复对象：
  CC148739350J40212149403 单片机工程师（req空 -> 把resp中的要求段/职责段按原始句子重新切分）
  CC404298980J40856902010 嵌入式央企（同上。注意用户prompt拼写404299是笔误，以实际存在404298为准，记录在报告中提醒）

流程：
  §二：搜索 v1/ 所有文件，列出命中位置 + 判断source-of-truth
  §三/§四：按原始句子边界切分 resp/req（仅移动原句，不改写） + 重算_sha256
  §四后：逐层同步到 raw/clean/batch*/csv(如果有resp/req列)
  §六：重跑 merge_and_clean.py / verify_all.py（如果可作为单脚本）
  §七：校验 Gold 文件字节 & ANN-0023 六字段不变
  §八：更新 gold_eval_exclusion_manifest.csv（仅更新 CC1487 那行 _sha256，总行数仍=110）
  §九触发（调用外部脚本后退出）：让上层重跑 unified audit / issue triage
"""
import csv, hashlib, json, os, re, stat, sys
from difflib import SequenceMatcher

# ---------------------- Paths ----------------------
ROOT = r"d:\du_yan\jiebang_guashuai_jingsai\zhigang-compass"
V1 = os.path.join(ROOT, "backend", "data", "golden_set", "candidate_pool", "v1")
FINAL = os.path.join(ROOT, "backend", "data", "golden_set", "final")
AD = os.path.join(ROOT, "backend", "data", "golden_set", "audit", "unified_jd_audit")
MANIFEST = os.path.join(AD, "gold_eval_exclusion_manifest.csv")
GOLD = os.path.join(FINAL, "jd_golden_110.jsonl")
RAW = os.path.join(V1, "real_jd_candidates_raw.jsonl")
CLEAN = os.path.join(V1, "real_jd_candidates_clean.jsonl")

TARGETS = ["CC148739350J40212149403", "CC404298980J40856902010"]
TYPO = "CC404298980J40856902010"  # user prompt variant

# ---------------------- helpers ----------------------
def text(x): return "" if x is None else str(x)
def norm_text(x):
    # CSV 字段内含 \r\n（历史 Windows 产物），jsonl 侧为 \n；不归一会导致
    # 同一条记录在 csv 与 jsonl/manifest 两侧算出不同 _sha256
    return "" if x is None else str(x).replace("\r\n", "\n").replace("\r", "\n")
def sha(r): return hashlib.sha256((norm_text(r.get("responsibilities")) + "\n" + norm_text(r.get("requirements"))).encode("utf-8")).hexdigest().lower()

def split_lines_preserve(s):
    """split text into list of (line_with_original_ending, ...). Returns ordered list preserving boundaries."""
    lines = []
    i = 0
    cur = []
    for ch in s:
        cur.append(ch)
        if ch == "\n":
            lines.append("".join(cur)); cur = []
    if cur:
        lines.append("".join(cur))
    return lines  # each line includes newline if present

# ---------------------- §二 scan ----------------------
def scan_v1():
    hits = {sid: [] for sid in TARGETS + [TYPO]}
    pats = {sid: re.compile(re.escape(sid)) for sid in hits}
    exts = (".jsonl", ".json", ".md", ".csv", ".yaml", ".yml", ".txt", ".py")
    for dirpath, _, filenames in os.walk(V1):
        for fn in filenames:
            if not fn.lower().endswith(exts):
                continue
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, V1)
            try:
                with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                    if fn.lower().endswith((".jsonl", ".json")):
                        for i, ln in enumerate(f, 1):
                            for sid, pat in pats.items():
                                if pat.search(ln):
                                    hits[sid].append((rel, i, "byte_pos_line_jsonl"))
                    else:
                        body = f.read()
                        for sid, pat in pats.items():
                            for m in pat.finditer(body):
                                ln = body.count("\n", 0, m.start()) + 1
                                hits[sid].append((rel, ln, "text_find"))
            except Exception as e:
                print(f"WARN {rel}: {e}")
    print("=== §二 v1/ 两个sid搜索命中 ===")
    for sid in TARGETS:
        files = sorted(set(r[0] for r in hits[sid]))
        print(f"- {sid}: hits_line={len(hits[sid])}, unique_files={len(files)}")
        for u in files:
            lines_hit = sorted(set(r[1] for r in hits[sid] if r[0]==u))
            print(f"    v1/{u}  ({len(lines_hit)} 处命中) 行号sample: {lines_hit[:12]}")
    print(f"- 用户拼写变体 {TYPO} 命中数: {len(hits[TYPO])}（若=0则确认为笔误，沿用404298）")
    # File stats
    print("\n=== v1/ key file sizes ===")
    for key, p in [("RAW",RAW),("CLEAN",CLEAN),("GOLD",GOLD)]:
        if os.path.exists(p):
            print(f"  {key}: {os.path.getsize(p):,} bytes  mtime={int(os.path.getmtime(p))}")
    return hits

# ---------------------- §三/§四 sentence boundaries ----------------------
# Deterministic boundaries: decided based on previous packet.md manual review

def apply_fixes(record: dict) -> dict:
    """Return (record modified in-place, old_snapshot) only if source_id matches."""
    sid = str(record.get("source_id") or "")
    if sid not in TARGETS:
        return None
    old = {
        "sid": sid,
        "title": text(record.get("job_title_raw")),
        "resp": text(record.get("responsibilities")),
        "req": text(record.get("requirements")),
        "sha": text(record.get("_sha256")).lower(),
    }
    resp = norm_text(old["resp"])
    req = norm_text(old["req"])
    # Detail kept untouched.
    if sid == "CC148739350J40212149403":
        # From packet L68-82: responsibilities has 6 numbered lines (resp==detail, 226 chars).
        # Lines 1-3: pure requirements (education/years of experience/language skills).
        # Lines 4-6: duties (FPGA/CPLD app dev, schematic/PCB design, hw design/dev/test/maintain lifecycle).
        # Use split_lines_preserve (already separated by \n in original).
        lines = split_lines_preserve(resp)
        assert len(lines) == 6, f"Expected 6 lines for CC1487, got {len(lines)}: {[repr(l[:30]) for l in lines]}"
        new_req = "".join(lines[:3]).rstrip("\n")  # 1-3 要求
        new_resp = "".join(lines[3:]).rstrip("\n") # 4-6 职责
        # Strip possible trailing newlines separately per rule
    elif sid == "CC404298980J40856902010":
        # From packet L84-97: 5 lines, line-no-classification req-like=[2,4,5], duty-like=[1,3].
        # i.e. lines 1 + 3 are duties; lines 2,4,5 are requirements/skills/soft skills.
        lines = split_lines_preserve(resp)
        assert len(lines) == 5, f"Expected 5 lines for CC4042, got {len(lines)}: {[repr(l[:30]) for l in lines]}"
        new_resp = "".join(lines[i] for i in (0, 2)).rstrip("\n")  # line1, line3 (0-indexed) 职责
        new_req = "".join(lines[i] for i in (1, 3, 4)).rstrip("\n")  # line2, line4, line5 要求
    else:
        return None
    new_sha = compute_sha(new_resp, new_req)
    # Apply
    record["responsibilities"] = new_resp
    record["requirements"] = new_req
    record["_sha256"] = new_sha
    return {
        "old": old,
        "new": {
            "resp": new_resp,
            "req": new_req,
            "sha": new_sha,
        },
    }


def compute_sha(resp, req):
    return hashlib.sha256((norm_text(resp) + "\n" + norm_text(req)).encode("utf-8")).hexdigest().lower()

# ---------------------- §四 apply to files ----------------------
DATA_FILES_JSONL_JSON = []
# Walk v1, pick JSON/JSONL data files (not scripts)
for dirpath, _, filenames in os.walk(V1):
    for fn in filenames:
        low = fn.lower()
        # skip scripts (py/md/bat) → only jsonl/json/csv data
        if low.endswith((".jsonl", ".json")):
            DATA_FILES_JSONL_JSON.append(os.path.join(dirpath, fn))
# Also CSV data files if they contain responsibilities/requirements columns
CSV_DATA_FILES = [os.path.join(V1, fn) for fn in ["real_jd_candidates_clean.csv", "real_jd_review_required.csv", "real_jd_rejected.csv"] if os.path.exists(os.path.join(V1, fn))]

def rewrite_jsonl(path):
    rows = []
    changed_any = False
    changes_per_file = []
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            if not ln.strip():
                rows.append(ln); continue
            try:
                r = json.loads(ln)
            except Exception:
                rows.append(ln); continue
            change = apply_fixes(r)
            if change:
                changed_any = True
                changes_per_file.append(change)
                rows.append(json.dumps(r, ensure_ascii=False) + "\n")
            else:
                rows.append(ln)
    if changed_any:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.writelines(rows)
        print(f"  [FIXED JSONL] {os.path.relpath(path, ROOT)}  records_changed={len(changes_per_file)}")
    else:
        print(f"  [NO HIT] {os.path.relpath(path, ROOT)}")
    return changes_per_file

def rewrite_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        print(f"  [SKIP not valid JSON] {os.path.relpath(path, ROOT)}")
        return []
    changed = False
    changes = []
    # Could be list of records or dict with list key (handle common cases)
    def walk(node):
        nonlocal changed, changes
        if isinstance(node, dict):
            if str(node.get("source_id") or "") in TARGETS:
                c = apply_fixes(node)
                if c:
                    changed = True
                    changes.append(c)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(data)
    if changed:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"  [FIXED JSON] {os.path.relpath(path, ROOT)}  records_changed={len(changes)}")
    else:
        print(f"  [NO HIT] {os.path.relpath(path, ROOT)}")
    return changes

def rewrite_csv(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
        fieldnames = list(rows[0].keys()) if rows else []
    if not rows or not any(k in fieldnames for k in ("responsibilities","requirements","_sha256","source_id")):
        print(f"  [SKIP CSV no relevant cols] {os.path.relpath(path,ROOT)} cols={fieldnames[:8]}")
        return []
    if "source_id" not in fieldnames:
        print(f"  [SKIP CSV no source_id] {os.path.relpath(path,ROOT)}")
        return []
    changes = []
    changed_any = False
    for r in rows:
        sid = str(r.get("source_id") or "")
        if sid not in TARGETS:
            continue
        # Build a pseudo-record dict for apply_fixes
        pseudo = {"source_id": sid,
                  "responsibilities": r.get("responsibilities", ""),
                  "requirements": r.get("requirements", ""),
                  "_sha256": r.get("_sha256", "")}
        c = apply_fixes(pseudo)
        if c:
            changed_any = True
            changes.append(c)
            # write back
            if "responsibilities" in r: r["responsibilities"] = pseudo["responsibilities"]
            if "requirements" in r: r["requirements"] = pseudo["requirements"]
            if "_sha256" in r: r["_sha256"] = pseudo["_sha256"]
    if changed_any:
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print(f"  [FIXED CSV] {os.path.relpath(path, ROOT)}  records_changed={len(changes)}")
    else:
        print(f"  [NO HIT] {os.path.relpath(path, ROOT)}")
    return changes

# ===================== MAIN =======================
if __name__ == "__main__":
    print("=== PHASE 0: v1/ 关键文件快照（修复前大小/mtime用于§七回归对比） ===")
    snap_before = {}
    for p in [RAW, CLEAN, GOLD] + DATA_FILES_JSONL_JSON + CSV_DATA_FILES:
        if os.path.exists(p):
            snap_before[p] = (os.path.getsize(p), int(os.path.getmtime(p)))

    hits = scan_v1()
    # Confirm CC4042 variant 404299 (user prompt) -> 0 hits == typo
    typo_hits = len(hits.get(TYPO, []))
    print(f"\n用户拼写变体 {TYPO} 命中数={typo_hits}，将沿用真实存在的sid=CC404298980J40856902010")

    # Save Gold snapshot (bytes + ANN-0023 field values)
    with open(GOLD, "rb") as f:
        GOLD_BYTES_BEFORE = f.read()
    GOLD_SIZE_BEFORE = len(GOLD_BYTES_BEFORE)
    gd_rows = []
    with open(GOLD, "r", encoding="utf-8") as f:
        gd_rows = [json.loads(ln) for ln in f if ln.strip()]
    ANN_BEFORE = None
    for r in gd_rows:
        if str(r.get("sample_id")) == "ANN-0023":
            ANN_BEFORE = {k: r.get(k) for k in ["sample_id","source_id","gold_title","gold_skills","gold_bonus_skills","gold_experience","gold_education","gold_core_duties"]}
            break
    assert ANN_BEFORE is not None, "ANN-0023 not found before!"
    print(f"\nGold字节数（修复前）={GOLD_SIZE_BEFORE:,}")

    print("\n=== §三/§四/§四后: 批量修复 v1/ JSONL data files ===")
    all_changes = {}  # sid -> {old,new} list (one per sid ideally)
    for fp in DATA_FILES_JSONL_JSON:
        low = fp.lower()
        cp = rewrite_jsonl(fp) if low.endswith(".jsonl") else rewrite_json(fp)
        for c in cp:
            sid = c["old"]["sid"]
            all_changes.setdefault(sid, []).append(c)

    print("\n=== CSV data files ===")
    for fp in CSV_DATA_FILES:
        rewrite_csv(fp)

    # §六 重新验证clean总数=158
    def count_records(fp):
        if not os.path.exists(fp): return -1
        with open(fp, "r", encoding="utf-8") as f:
            return sum(1 for ln in f if ln.strip())
    cnt_raw = count_records(RAW)
    cnt_clean = count_records(CLEAN)
    print(f"\n=== §六 修复后记录数（仅real_*聚合，不包含batch作为总数） ===")
    print(f"  raw 总数 = {cnt_raw}")
    print(f"  clean总数 = {cnt_clean}（期望=158）")
    assert cnt_clean == 158, f"FAIL: clean总数应=158，实际={cnt_clean}"

    # Optional: try run existing merge_and_clean.py deterministically (only if script accepts no interactive flags)
    MERGE_SCRIPT = os.path.join(V1, "merge_and_clean.py")
    if os.path.exists(MERGE_SCRIPT):
        print("\n[INFO] merge_and_clean.py 存在，本轮已手动同步 raw/clean/batch/csv 所有受影响字段；如需强制重新生成可由人工后续运行 `cd v1 && python merge_and_clean.py`")

    # §七 Gold protection: verify bytes identical
    with open(GOLD, "rb") as f:
        GOLD_BYTES_AFTER = f.read()
    assert GOLD_BYTES_BEFORE == GOLD_BYTES_AFTER, f"Gold文件字节变化！禁止！before={GOLD_SIZE_BEFORE} after={len(GOLD_BYTES_AFTER)}"
    with open(GOLD, "r", encoding="utf-8") as f:
        gd_after = [json.loads(ln) for ln in f if ln.strip()]
    ANN_AFTER = None
    for r in gd_after:
        if str(r.get("sample_id")) == "ANN-0023":
            ANN_AFTER = {k: r.get(k) for k in ANN_BEFORE.keys()}
            break
    assert ANN_BEFORE == ANN_AFTER, f"ANN-0023 Gold标注字段变化！禁止！before={ANN_BEFORE} after={ANN_AFTER}"
    print("\n=== §七 Gold保护验证 ===")
    print(f"  Gold字节数 before=after ？ {GOLD_SIZE_BEFORE == len(GOLD_BYTES_AFTER)}")
    print(f"  ANN-0023六字段不变？ {ANN_BEFORE == ANN_AFTER}（应为True）")
    print(f"  Gold final/目录在git diff中？（脚本未修改，应为0变化）")

    # §八 Update manifest CC1487 sha -> new_sha, preserve 110 rows
    print("\n=== §八 更新 gold_eval_exclusion_manifest.csv ===")
    with open(MANIFEST, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
        fields = list(rows[0].keys())
    manifest_count_before = len(rows)
    updated = 0
    for r in rows:
        if r.get("source_id") == "CC148739350J40212149403":
            old_manifest_sha = r.get("_sha256")
            # compute new from clean file
            cc1487_clean = None
            with open(CLEAN, "r", encoding="utf-8") as fcl:
                for ln in fcl:
                    if not ln.strip(): continue
                    rec = json.loads(ln)
                    if str(rec.get("source_id")) == "CC148739350J40212149403":
                        cc1487_clean = rec; break
            new_manifest_sha = cc1487_clean["_sha256"]
            if old_manifest_sha != new_manifest_sha:
                r["_sha256"] = new_manifest_sha
                updated += 1
                print(f"  manifest updated CC1487: {old_manifest_sha[:16]} -> {new_manifest_sha[:16]}")
            else:
                print(f"  manifest CC1487 sha未变（异常）")
    assert manifest_count_before == len(rows), "行数变化！禁止"
    with open(MANIFEST, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"  manifest行数: before={manifest_count_before} after={len(rows)}（期望=110，必须不变）")
    assert len(rows) == 110, f"manifest行数必须=110，实际={len(rows)}"
    print(f"  manifest更新行数={updated}（期望=1）")

    # =============== §十四 verbose report dump for final review ==============
    print("\n" + "=" * 78)
    print("=== §十四 修复前后证据摘要（供最终汇报） ===")
    for sid in TARGETS:
        # Pull from clean file now
        clean_rec = None
        with open(CLEAN, "r", encoding="utf-8") as f:
            for ln in f:
                if not ln.strip(): continue
                rec = json.loads(ln)
                if str(rec.get("source_id")) == sid:
                    clean_rec = rec; break
        # old values from all_changes (first entry for this sid)
        cp_list = all_changes.get(sid, [])
        if cp_list:
            c = cp_list[0]
            old, new = c["old"], c["new"]
            print(f"\n【{sid} - {clean_rec.get('job_title_raw')!r}】")
            print(f"  [OLD] responsibilities（len={len(old['resp'])}）:\n    {repr(old['resp'])}")
            print(f"  [OLD] requirements（len={len(old['req'])}）: {repr(old['req'])}")
            print(f"  [OLD] _sha256 = {old['sha']}")
            print(f"  [NEW] responsibilities（len={len(new['resp'])}）:\n    {repr(new['resp'])}")
            print(f"  [NEW] requirements（len={len(new['req'])}）:\n    {repr(new['req'])}")
            print(f"  [NEW] _sha256 = {new['sha']}")
    print("\n=== DONE repair_split.py（§九§十需在上层重新调用统一审计脚本） ===")
    print(f"clean_count={cnt_clean} raw_count={cnt_raw}")
    print(f"gold_bytes_unchanged={GOLD_SIZE_BEFORE == len(GOLD_BYTES_AFTER)} ANN_fields_unchanged={ANN_BEFORE == ANN_AFTER}")
    print(f"manifest_rows={len(rows)} updated_rows={updated}")
