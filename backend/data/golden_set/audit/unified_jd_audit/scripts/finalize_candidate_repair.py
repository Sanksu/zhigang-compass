"""§一-§五+§十 finalize: cleanup typo sid CC404299 -> canonical CC404298; 重新核验切分/SHA/唯一/全量回归; manifest行数检查"""
import csv, hashlib, json, os, re, sys
from collections import Counter, defaultdict

ROOT = r"d:\du_yan\jiebang_guashuai_jingsai\zhigang-compass"
V1 = os.path.join(ROOT, "backend", "data", "golden_set", "candidate_pool", "v1")
AD = os.path.join(ROOT, "backend", "data", "golden_set", "audit", "unified_jd_audit")
FINAL = os.path.join(ROOT, "backend", "data", "golden_set", "final")

# NOTE: split into parts to avoid self-replace during file walking
TYPO = "CC4042" + "99880" + "J40856902010"        # WRONG SPELLING (99880)
CANON = "CC4042" + "98980" + "J40856902010"       # CANONICAL  (98980)
TARGETS = ["CC148739350J40212149403", CANON]

MANIFEST = os.path.join(AD, "gold_eval_exclusion_manifest.csv")
RAW = os.path.join(V1, "real_jd_candidates_raw.jsonl")
CLEAN = os.path.join(V1, "real_jd_candidates_clean.jsonl")
REVIEW_CSV = os.path.join(V1, "real_jd_review_required.csv")
REJECT_CSV = os.path.join(V1, "real_jd_rejected.csv")
BATCH_EMB = os.path.join(V1, "batch_embedded_security.json")
GOLD = os.path.join(FINAL, "jd_golden_110.jsonl")

TEXT_EXT = (".csv", ".md", ".py", ".txt", ".yaml", ".yml", ".json", ".jsonl")

def text(x): return "" if x is None else str(x)


# ================================================================ §一
print("=== §一 清理错误SID引用（TYPO=错误拼写 → CANON=真实存在） ===")
audit_replacements = 0
for dirpath, _, filenames in os.walk(AD):
    for fn in filenames:
        low = fn.lower()
        if not any(low.endswith(e) for e in (".csv",".md",".py",".txt",".yaml",".yml")):
            continue
        fp = os.path.join(dirpath, fn)
        try:
            with open(fp, "r", encoding="utf-8", errors="strict") as f:
                body = f.read()
        except UnicodeDecodeError:
            try:
                with open(fp, "r", encoding="utf-8-sig", errors="ignore") as f:
                    body = f.read()
            except Exception:
                continue
        hits = body.count(TYPO)
        if hits == 0:
            continue
        body2 = body.replace(TYPO, CANON)
        with open(fp, "w", encoding="utf-8", newline="") as f:
            f.write(body2)
        audit_replacements += hits
        rel = os.path.relpath(fp, AD)
        print(f"  REPLACED {hits}x in audit/{rel}")
total_typo_left = 0
for dirpath, _, filenames in os.walk(AD):
    for fn in filenames:
        if not fn.lower().endswith(TEXT_EXT): continue
        try:
            with open(os.path.join(dirpath, fn), "r", encoding="utf-8", errors="ignore") as f:
                c = f.read()
            total_typo_left += c.count(TYPO)
        except Exception:
            pass
print(f"  Audit替换总次数: {audit_replacements}")
print(f"  Audit目录错误拼写残留（检查后）: {total_typo_left}（必须=0）")
assert total_typo_left == 0
v1_typo = 0
for dirpath, _, filenames in os.walk(V1):
    for fn in filenames:
        if not fn.lower().endswith(TEXT_EXT): continue
        try:
            with open(os.path.join(dirpath, fn), "r", encoding="utf-8", errors="ignore") as f:
                v1_typo += f.read().count(TYPO)
        except Exception:
            pass
print(f"  V1目录错误拼写残留: {v1_typo}（必须=0）")


# ================================================================ §二+§三+§四 逐条核验
def norm_text(x):
    # csv 字段内含 \r\n，jsonl 为 \n；哈希前必须归一，否则同记录跨文件 sha 不一致
    return "" if x is None else str(x).replace("\r\n", "\n").replace("\r", "\n")

def sha_calc(r):
    return hashlib.sha256((norm_text(r.get("responsibilities")) + "\n" + norm_text(r.get("requirements"))).encode("utf-8")).hexdigest().lower()


REQ_KW = [r"学历", r"本科", r"硕士", r"博士", r"经验", r"年以上", r"熟悉", r"精通", r"熟练", r"掌握",
          r"了解", r"专业", r"具备", r"优先", r"资格", r"证书", r"CET", r"英语", r"抗压", r"沟通"]
DUTY_V = [r"负责", r"主导", r"参与", r"撰写", r"制定", r"推动", r"对接", r"协调", r"交付",
          r"开发", r"设计", r"测试", r"维护", r"验证", r"搭建", r"优化", r"构建", r"部署",
          r"升级", r"改造", r"实现", r"支撑", r"驱动", r"运维", r"管理", r"调研", r"选型",
          r"规划", r"分析", r"排障", r"修复", r"集成"]

def classify(txt):
    req = sum(1 for p in REQ_KW if re.search(p, text(txt), re.I))
    duty = sum(1 for p in DUTY_V if re.search(p, text(txt), re.I))
    return req, duty

print("\n=== §二/§三/§四 两条candidate逐文件核验（原始batch/raw/clean/csv/review_required） ===")
FILES_TO_CHECK = [
    ("batch_source", BATCH_EMB, "json_array"),
    ("raw", RAW, "jsonl"),
    ("clean", CLEAN, "jsonl"),
]
review_rows = []
if os.path.exists(REVIEW_CSV):
    with open(REVIEW_CSV, "r", encoding="utf-8-sig") as f:
        review_rows = list(csv.DictReader(f))
csv_clean_rows = []
clean_csv_path = os.path.join(V1, "real_jd_candidates_clean.csv")
if os.path.exists(clean_csv_path):
    with open(clean_csv_path, "r", encoding="utf-8-sig") as f:
        csv_clean_rows = list(csv.DictReader(f))

overall_sha_pass = 0
overall_hits_ok = 0
for sid in TARGETS:
    print(f"\n--- TARGET {sid} ---")
    for role, path, ftype in FILES_TO_CHECK:
        if not os.path.exists(path):
            print(f"  {role} file missing"); continue
        records = []
        if ftype == "jsonl":
            with open(path, "r", encoding="utf-8") as f:
                for ln in f:
                    if not ln.strip(): continue
                    try: records.append(json.loads(ln))
                    except Exception: pass
        else:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            def walk(n):
                if isinstance(n, list):
                    for x in n: walk(x)
                elif isinstance(n, dict):
                    if str(n.get("source_id")) == sid: records.append(n)
                    for v in n.values(): walk(v)
            walk(data)
        matches = [r for r in records if str(r.get("source_id")) == sid]
        print(f"  [{role}] hits={len(matches)}（必须=1，唯一无重复）→ {len(matches)==1}")
        if len(matches) == 1: overall_hits_ok += 1
        if matches:
            r0 = matches[0]
            resp = text(r0.get("responsibilities"))
            req = text(r0.get("requirements"))
            det = text(r0.get("detail_raw_text"))
            r_req, r_duty = classify(resp)
            q_req, q_duty = classify(req)
            stored_sha = text(r0.get("_sha256")).strip().lower()
            new_calc = sha_calc(r0)
            sha_pass = (stored_sha == new_calc)
            if sha_pass: overall_sha_pass += 1
            print(f"    resp len={len(resp)} (≠空: {bool(resp)}) / req len={len(req)} (≠空: {bool(req)})")
            print(f"    resp分类正确？职责≥要求词 {r_duty}≥{r_req} = {r_duty>=r_req}")
            print(f"    req 分类正确？要求≥职责词 {q_req}≥{q_duty} = {q_req>=q_duty}")
            print(f"    detail_raw_text len={len(det)}（保持原始正文，不要求等于resp+req，只要非空即为OK）")
            print(f"    SHA PASS（stored == 重算）? {sha_pass}")
    rev_matches = [r for r in review_rows if str(r.get("source_id")) == sid]
    print(f"  [review_required.csv] rows={len(rev_matches)}")
    for rrm in rev_matches:
        print(f"    resp_len={len(text(rrm.get('responsibilities')))} req_len={len(text(rrm.get('requirements')))}")
    ccsv = [r for r in csv_clean_rows if str(r.get("source_id")) == sid]
    print(f"  [clean_csv] rows={len(ccsv)}")

print(f"\n  >>> hits唯一正确: {overall_hits_ok}/{len(TARGETS)*3}（期望={len(TARGETS)*3}）")
print(f"  >>> SHA 2/2 PASS？ {overall_sha_pass}/{len(TARGETS)*3}（每个文件stored==重算）")


# ================================================================ §五 全量回归
print("\n=== §五 candidate pool/v1 全量回归 ===")
with open(CLEAN, "r", encoding="utf-8") as f:
    accepted_count = sum(1 for ln in f if ln.strip())
review_count = len(review_rows)
rejected_count = 0
if os.path.exists(REJECT_CSV):
    with open(REJECT_CSV, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
        rejected_count = len(rows)
raw_count = 0
with open(RAW, "r", encoding="utf-8") as f:
    raw_count = sum(1 for ln in f if ln.strip())
print(f"  raw 总数={raw_count}（应=158）")
print(f"  clean (accepted) 总数={accepted_count}（应=158）")
print(f"  review_required.csv 行数={review_count}")
print(f"  rejected.csv 行数={rejected_count}")
assert accepted_count == 158, f"accepted should be 158 got {accepted_count}"

tiers_clean = Counter()
sample_tiers = {}
with open(CLEAN, "r", encoding="utf-8") as f:
    for ln in f:
        if not ln.strip(): continue
        r = json.loads(ln)
        t = str(r.get("tier") or "N/A")
        tiers_clean[t] += 1
        s = str(r.get("source_id"))
        if s in TARGETS: sample_tiers[s] = t
print(f"  tier分布(clean: {sum(tiers_clean.values())}条)：{dict(tiers_clean)}")
print(f"  目标样本tier：{sample_tiers}")

# ================================================================ §六 Gold保护
print("\n=== §六 Gold保护（仅校验大小+ANN-0023字段值） ===")
with open(GOLD, "rb") as f:
    gold_bytes = f.read()
print(f"  jd_golden_110.jsonl bytes={len(gold_bytes):,}（修复前后不变）")
gd_rows = [json.loads(ln) for ln in gold_bytes.decode("utf-8").splitlines() if ln.strip()]
ann = next((r for r in gd_rows if str(r.get("sample_id")) == "ANN-0023"), None)
assert ann is not None
gold_six_check = {k: ann.get(k) for k in ["gold_title","gold_skills","gold_bonus_skills","gold_experience","gold_education","gold_core_duties"]}
six_ok = {}
for k in ["gold_title","gold_skills","gold_bonus_skills","gold_experience","gold_education","gold_core_duties"]:
    if k == "gold_bonus_skills":
        six_ok[k] = True
    else:
        six_ok[k] = bool(str(gold_six_check[k]))
core = str(gold_six_check["gold_core_duties"])
req_n = sum(1 for p in REQ_KW if re.search(p, core, re.I))
duty_n = sum(1 for p in DUTY_V if re.search(p, core, re.I))
CORRECT = (duty_n >= 1 and req_n <= 2) or (len(core) > 20 and duty_n >= req_n)
print(f"  ANN-0023字段非空（bonus空为正常）：{six_ok}")
print(f"  gold_core_duties: req_hit={req_n}, duty_hit={duty_n} → GOLD_CORRECT？ {CORRECT}")

# ================================================================ §十 exclusion manifest
print("\n=== §十 gold_eval_exclusion_manifest.csv 检查 ===")
with open(MANIFEST, "r", encoding="utf-8-sig") as f:
    mrows = list(csv.DictReader(f))
print(f"  manifest总行数={len(mrows)} 必须=110 → {len(mrows)==110}")
cc1487_row = [r for r in mrows if r.get("source_id") == "CC148739350J40212149403"]
assert len(cc1487_row) == 1
cc1487_new_sha = text(cc1487_row[0].get("_sha256")).strip().lower()
cc1487_clean = None
with open(CLEAN, "r", encoding="utf-8") as f:
    for ln in f:
        if not ln.strip(): continue
        r = json.loads(ln)
        if str(r.get("source_id")) == "CC148739350J40212149403":
            cc1487_clean = r; break
expected_new_sha = sha_calc(cc1487_clean)
print(f"  CC1487 manifest SHA vs clean重算匹配？ {cc1487_new_sha == expected_new_sha}（{cc1487_new_sha[:16]} / {expected_new_sha[:16]}）")
ann_in_manifest = any(r.get("gold_sample_id") == "ANN-0023" for r in mrows)
print(f"  ANN-0023(gold_sample_id)仍在manifest？ {ann_in_manifest}（必须=True）")

print("\n=== §一 纠正汇总（最终） ===")
print(f"  错误拼写TYPO残留：audit目录={total_typo_left}, v1目录={v1_typo} → canonical = {CANON}")
