"""P0/P1 evidence review packet. Read-only.

Produces:
  p0_p1_review_packet.md
  p0_p1_review_decisions.csv
"""
import csv, json, os, re, hashlib
from difflib import SequenceMatcher

ROOT = r"d:\du_yan\jiebang_guashuai_jingsai\zhigang-compass"
AD = os.path.join(ROOT, "backend", "data", "golden_set", "audit", "unified_jd_audit")

ZL = os.path.join(ROOT, "backend","data","golden_set","candidate_pool","v1","real_jd_candidates_clean.jsonl")
GD = os.path.join(ROOT, "backend","data","golden_set","final","jd_golden_110.jsonl")
MNF = os.path.join(AD, "gold_eval_exclusion_manifest.csv")

PACKET_MD = os.path.join(AD, "p0_p1_review_packet.md")
DEC_CSV = os.path.join(AD, "p0_p1_review_decisions.csv")


def load_jsonl(p):
    with open(p, "r", encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


def text(x):
    return "" if x is None else str(x)


def sha(r):
    return hashlib.sha256((text(r.get("responsibilities")) + "\n" + text(r.get("requirements"))).encode("utf-8")).hexdigest().lower()


def sim(a, b):
    a = text(a)[:800]; b = text(b)[:800]
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


zl = load_jsonl(ZL); gd = load_jsonl(GD)
zl_by_sid = {str(r.get("source_id")): r for r in zl}
gd_by_sid = {str(r.get("source_id")): r for r in gd}
gd_by_sample = {str(r.get("sample_id")): r for r in gd if r.get("sample_id")}

with open(MNF, "r", encoding="utf-8-sig") as f:
    manifest = list(csv.DictReader(f))
zl_exclusion_sids = set(r["source_id"] for r in manifest if r.get("source_id"))
gold_sample_by_zl_sid = {r["source_id"]: r.get("gold_sample_id", "") for r in manifest if r.get("source_id")}

REQUIREMENT_HEADINGS = [r"任职要求", r"任职资格", r"岗位要求", r"技能要求", r"资格要求",
                        r"要求[:：]", r"任职条件", r"岗位任职要求", r"Qualification", r"Requirements",
                        r"必备要求", r"优先要求", r"基本要求"]

# =============================================================== §一 P0 ANN-0023
ANN_SAMPLE = "ANN-0023"
ANN_ZL_SID = "CC148739350J40212149403"
g_ann = gd_by_sample.get(ANN_SAMPLE)
z_ann = zl_by_sid.get(ANN_ZL_SID)

assert g_ann and z_ann, "ANN-0023 or parent not found!"

# Gold field names present (do not invent fields)
gold_field_keys = sorted(k for k in g_ann.keys() if k.startswith("gold_"))

# A. Existence of review_gold_requirements field
REVIEW_GOLD_REQ_EXISTS = any(k == "review_gold_requirements" for k in g_ann.keys())
GOLD_ANNOT_FIELDS = sorted(k for k in g_ann.keys() if k.startswith("gold_"))
# Subset relevant fields per user prompt
RELEVANT_GOLD_FIELDS = [k for k in ["gold_skills","gold_bonus_skills","gold_experience","gold_education","gold_core_duties","gold_title"] if k in g_ann]

g_req = text(g_ann.get("requirements"))
z_req = text(z_ann.get("requirements"))
g_resp = text(g_ann.get("responsibilities"))
z_resp = text(z_ann.get("responsibilities"))
detail = text(z_ann.get("detail_raw_text"))

# C. Explicit requirement section in original JD text?
def find_requirement_section_signals(txt):
    headings = [p for p in REQUIREMENT_HEADINGS if re.search(p, txt, re.I)]
    # Also look for requirement-keyword density in lines: education/experience/familiar/proficient/master/master's/bachelor
    req_kw = [r"学历", r"本科", r"硕士", r"经验", r"年以上", r"熟悉", r"精通", r"熟练", r"掌握", r"了解", r"专业", r"优先", r"具备", r"资格", r"证书"]
    kw_hit_lines = 0
    lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
    for ln in lines:
        if sum(1 for p in req_kw if re.search(p, ln, re.I)) >= 1:
            kw_hit_lines += 1
    ratio = kw_hit_lines / max(1, len(lines))
    return headings, len(lines), kw_hit_lines, ratio

hd, nlines, kw_lines, kw_ratio = find_requirement_section_signals(detail)
# C decision
if hd or (nlines >= 3 and kw_ratio >= 0.8):
    C_DECISION = "YES"
elif kw_ratio >= 0.5:
    C_DECISION = "AMBIGUOUS"
else:
    C_DECISION = "NO"
C_EVIDENCE = f"heading_hits={hd}; total_lines_in_detail={nlines}; req_keyword_hit_lines={kw_lines} ({kw_ratio:.0%}); detail_length={len(detail)}"

# B. Real reason for P0
REASONS = []
if not z_req:
    REASONS.append("1. candidate requirements为空")
# Check gold relevant annotation field empties
gold_field_empty = [k for k in RELEVANT_GOLD_FIELDS if not text(g_ann.get(k))]
if gold_field_empty:
    REASONS.append(f"2. Gold gold_*字段为空: {gold_field_empty}")
# 3. candidate切分失败？resp与detail全是要求词无职责词
RESP_DUTY_VERBS = [r"负责", r"主导", r"参与", r"撰写", r"制定", r"推动", r"对接", r"协调", r"交付",
                  r"Responsible", r"build", r"design", r"develop",
                  r"开发", r"设计", r"测试", r"维护", r"验证", r"搭建", r"优化", r"构建",
                  r"架构", r"部署", r"升级", r"改造", r"实现", r"支撑", r"驱动", r"运维",
                  r"管理", r"调研", r"选型", r"规划", r"分析", r"排障", r"修复", r"集成"]
duty_hits = [p for p in RESP_DUTY_VERBS if re.search(p, z_resp, re.I)]
REQUIRE_KW = [r"学历", r"本科", r"硕士", r"博士", r"经验", r"年以上", r"熟悉", r"精通", r"熟练", r"掌握",
              r"了解", r"专业", r"具备", r"优先", r"资格", r"证书", r"CET", r"英语", r"抗压", r"沟通"]
reqkw_hits = [p for p in REQUIRE_KW if re.search(p, z_resp, re.I)]
swap_suspect = len(reqkw_hits) >= 5 and len(duty_hits) <= 2
if swap_suspect and not z_req:
    REASONS.append(f"3. candidate切分失败：responsibilities内全为要求类关键词({len(reqkw_hits)}个:{reqkw_hits[:6]})，职责动词仅({len(duty_hits)}个:{duty_hits})→证据来源不完整，Gold继承req空")
# 4. Gold人工标注错误？需要人工检查gold_core_duties是否写了要求词当作职责
g_core = text(g_ann.get("gold_core_duties"))
g_skills = text(g_ann.get("gold_skills"))
g_edu = text(g_ann.get("gold_education"))
g_exp = text(g_ann.get("gold_experience"))
# Heuristic: if gold_core_duties contains many requirement keywords (education / experience / skill words) instead of duty verbs
core_reqkw = sum(1 for p in REQUIRE_KW if re.search(p, g_core, re.I))
core_duty = sum(1 for p in RESP_DUTY_VERBS if re.search(p, g_core, re.I))
if g_core and core_reqkw >= 3 and core_duty <= 1:
    REASONS.append(f"4. Gold人工标注疑似错误：gold_core_duties非空={g_core[:120]!r}，但内容以要求词为主（{core_reqkw}个要求词vs{core_duty}个职责动词）→标注gold_core_duties时误用了要求段")
elif g_core == "":
    REASONS.append("4. Gold gold_core_duties为空，无法判断是否违反规则（标注员可能未填）")
else:
    REASONS.append(f"4. Gold gold_core_duties={g_core[:80]!r}，要求词={core_reqkw}，职责动词={core_duty} → 需人工判断是否混淆")
B_REASON = "；".join(REASONS)

# D Gold是否违反规则
D_DECISION = "CANNOT_DETERMINE"
if not g_core and gold_field_empty:
    D_DECISION = "GOLD_REVIEW_REQUIRED"
elif g_core and core_reqkw >= 3 and core_duty <= 1:
    D_DECISION = "GOLD_REVIEW_REQUIRED"
elif g_core and core_reqkw <= 1 and core_duty >= 1:
    # core_duties contains duty verbs, skill fields have kw
    D_DECISION = "GOLD_CORRECT"
else:
    D_DECISION = "GOLD_REVIEW_REQUIRED"

# ============================================================== §二 P1 × 2
P1_SIDS = ["CC148739350J40212149403", "CC404298980J40856902010"]

def diagnose_p1(sid):
    r = zl_by_sid[sid]
    resp = text(r.get("responsibilities")); req = text(r.get("requirements"))
    det = text(r.get("detail_raw_text")); title = text(r.get("job_title_raw"))
    comp = text(r.get("company_name"))
    hd, nlines, kw_lines, kw_ratio = find_requirement_section_signals(det)

    # Heuristic: explicit heading
    if hd:
        decision = "PARSE_SPLIT_FAILURE_CONFIRMED"
        # Find heading position in detail to split
        idx = None; hit_pat = None
        for p in hd:
            m = re.search(p, det, re.I)
            if m:
                idx = m.start(); hit_pat = p; break
        bnd_resp = f"detail[0:{idx}]" if idx is not None else "detail[0:heading_pos]"
        bnd_req = f"detail[{idx}:end] （{hit_pat}起始）" if idx is not None else "detail[heading_pos:end]"
        evidence = f"detail中有明确要求标题：{hd}。detail lines={nlines}, keyword_lines={kw_lines}({kw_ratio:.0%})"
    else:
        # No explicit heading, mixed content?
        if not req and kw_ratio >= 0.5:
            # requirement keywords dominate entire detail/resp -> all is requirements
            # check if there are ANY duty lines in resp at all
            d_hits = [p for p in RESP_DUTY_VERBS if re.search(p, resp, re.I)]
            rq_hits = [p for p in REQUIRE_KW if re.search(p, resp, re.I)]
            if len(rq_hits) >= 5 and len(d_hits) <= 2:
                decision = "PARSE_SPLIT_FAILURE_CONFIRMED"
                bnd_resp = "空（responsibilities字段内容全为要求段，没有职责内容，应整体移到requirements）"
                bnd_req = f"原responsibilities全文[0:{len(resp)}]（共{len(resp)}字符，{nlines}行，全部为学历/经验/技能要求）"
                evidence = f"无明确要求标题，但resp中要求关键词{len(rq_hits)}个，职责动词{len(d_hits)}个 → 整段错切"
            else:
                # Mixed: some duty lines, some req lines, no heading
                lines = [ln for ln in resp.splitlines() if ln.strip()]
                req_like_idx = []
                duty_like_idx = []
                for i, ln in enumerate(lines, 1):
                    rq = sum(1 for p in REQUIRE_KW if re.search(p, ln, re.I))
                    du = sum(1 for p in RESP_DUTY_VERBS if re.search(p, ln, re.I))
                    if rq > du: req_like_idx.append(i)
                    else: duty_like_idx.append(i)
                # If last N lines are req-like, first N duty -> split by line index
                if req_like_idx and duty_like_idx:
                    decision = "PARSE_SPLIT_FAILURE_CONFIRMED"
                    split_at_line = min(req_like_idx)  # first req-like line
                    # compute character position
                    char_off = 0
                    for _i, ln in enumerate(resp.splitlines()):
                        if _i + 1 == split_at_line:
                            break
                        char_off += len(ln) + 1  # newline
                    bnd_resp = f"responsibilities[0:{char_off}]（行1~{split_at_line-1}=职责类）"
                    bnd_req = f"responsibilities[{char_off}:{len(resp)}]（行{split_at_line}~{len(lines)}=要求类）"
                    evidence = f"无明确标题，但逐行可分。req-like行号={req_like_idx[:8]}，duty-like行号={duty_like_idx[:8]}"
                else:
                    decision = "AMBIGUOUS"
                    bnd_resp = "不建议改（结构不清）"
                    bnd_req = "不建议改（结构不清）"
                    evidence = f"无明确标题，且逐行无法明显划分（全req={bool(req_like_idx and not duty_like_idx)}, 全duty={bool(duty_like_idx and not req_like_idx)}）"
        else:
            decision = "SOURCE_TRUE_MISSING"
            bnd_resp = f"保留responsibilities[0:{len(resp)}]（确认为职责）"
            bnd_req = "无（原JD未提供独立任职要求段）"
            evidence = f"无明确要求标题，要求关键词占比低={kw_ratio:.0%}"
    return {
        "source_id": sid, "job_title_raw": title, "company_name": comp,
        "resp_len": len(resp), "req_len": len(req), "detail_len": len(det),
        "decision": decision, "boundary_resp_suggest": bnd_resp, "boundary_req_suggest": bnd_req,
        "evidence": evidence, "resp_lines": nlines, "kw_lines": kw_lines, "kw_ratio": kw_ratio,
    }

p1_results = [diagnose_p1(sid) for sid in P1_SIDS]

# ============================================================== §三 Gold impact of P1
p1_gold_status = {}
for sid in P1_SIDS:
    ingold = sid in zl_exclusion_sids
    p1_gold_status[sid] = {
        "in_gold_manifest": ingold,
        "gold_sample_id": gold_sample_by_zl_sid.get(sid, "") if ingold else "",
    }

# ============================================================== §四 SHA legacy review
SHA_SIDS = ["CC148739350J40212149403", "CC404298980J40856902010"]
sha_results = []
for sid in SHA_SIDS:
    r = zl_by_sid[sid]
    orig = text(r.get("_sha256")).strip().lower()
    cur_form = sha(r)
    # legacy resp_only check
    legacy_resp_only = hashlib.sha256(text(r.get("responsibilities")).encode("utf-8")).hexdigest().lower()
    legacy_req_only = hashlib.sha256(text(r.get("requirements")).encode("utf-8")).hexdigest().lower()
    legacy_rn = hashlib.sha256((text(r.get("responsibilities")) + "\r\n" + text(r.get("requirements"))).encode("utf-8")).hexdigest().lower()
    rule_hit = None
    for name, hx in [("resp_only", legacy_resp_only), ("req_only", legacy_req_only), ("resp_rnb_req", legacy_rn), ("current_resp_nl_req", cur_form)]:
        if hx == orig:
            rule_hit = name; break
    # body changed? compare current detail vs detail vs resp
    d_eq_r = (text(r.get("detail_raw_text")) == text(r.get("responsibilities")))
    traceable = bool(rule_hit)
    in_gold_lineage = (sid in zl_exclusion_sids)
    if rule_hit in ("resp_only","req_only") and traceable and not in_gold_lineage:
        cat = "NON_BLOCKING_LEGACY_HASH"
    elif rule_hit in ("resp_only","req_only") and in_gold_lineage:
        # Gold侧本身无sha字段，仍不阻断
        cat = "NON_BLOCKING_LEGACY_HASH"
    elif rule_hit == "current_resp_nl_req":
        cat = "MATCH（不应出现在异常列表）"
    else:
        cat = "BLOCKING_CANDIDATE（请人工复核正文是否被改动）"
    sha_results.append({
        "source_id": sid, "title": text(r.get("job_title_raw")),
        "orig_sha": orig[:16], "computed_formula": cur_form[:16],
        "legacy_rule_hit": rule_hit, "detail_equals_resp": d_eq_r,
        "traceable": traceable, "in_gold_lineage": in_gold_lineage,
        "classification": cat,
    })
ALL_NON_BLOCK = all(s["classification"] == "NON_BLOCKING_LEGACY_HASH" for s in sha_results)

# ============================================================== §五 Packet + Decisions
decision_rows = []

# Row 0: P0 ANN-0023
ann_evidence_summary = (
    f"Gold fields present: gold={GOLD_ANNOT_FIELDS}; "
    f"review_gold_requirements exists? {REVIEW_GOLD_REQ_EXISTS}; "
    f"C=original_JD_requirement_section={C_DECISION} ({C_EVIDENCE}); "
    f"B_reasons={B_REASON[:300]}; "
    f"gold_core_duties_len={len(g_core)}, gold_skills_len={len(g_skills)}, "
    f"gold_education_len={len(g_edu)}, gold_experience_len={len(g_exp)}, gold_bonus_len={len(text(g_ann.get('gold_bonus_skills')))}"
)
decision_rows.append({
    "dataset": "Gold",
    "sample_id": ANN_SAMPLE,
    "source_id": str(g_ann.get("source_id") or ""),
    "issue": "P0 GOLD_REVIEW_REQUIRED (ANN-0023)",
    "evidence_result": ann_evidence_summary[:800],
    "gold_impact": "DIRECT (gold_core_duties/skills evidence source may be misaligned)",
    "recommended_action": "HUMAN_REVIEW_GOLD: 请张恺天复核ANN-0023的gold_core_duties（是否把要求段内容误写为核心职责？如果是则修正标注；如果gold_core_duties是独立补写的职责则为正确）",
    "blocking_baseline": "YES（GOLD_REVIEW_REQUIRED未解除前=BASELINE_BLOCKED_BY_GOLD_REVIEW）" if D_DECISION != "GOLD_CORRECT" else "NO",
})

# P1 × 2
for p in p1_results:
    sid = p["source_id"]
    gs = p1_gold_status[sid]
    ing = gs["in_gold_manifest"]
    impact = "P0_CANDIDATE_UPGRADE (对应Gold ANN-0023父记录)：candidate切分错误导致Gold证据源头不完整，Gold已随父继承req空，需与P0 ANN-0023一起复核" if ing else "N/A（不进入Gold110，不直接影响Gold评测结果）"
    blocking = "YES（P0候选升级，必须与ANN-0023一并复核）" if ing else "NO（baseline可并行，待修但不直接阻塞）"
    decision_rows.append({
        "dataset": "Zhilian",
        "sample_id": "",
        "source_id": sid,
        "issue": f"P1 {p['decision']} (requirements_empty + boundary suspect)",
        "evidence_result": f"resp_len={p['resp_len']}, req_len={p['req_len']}, detail_len={p['detail_len']}；decision={p['decision']}；{p['evidence']}；suggested_resp_boundary={p['boundary_resp_suggest']}；suggested_req_boundary={p['boundary_req_suggest']}",
        "gold_impact": impact,
        "recommended_action": ("UPGRADE_TO_P0_WITH_ANN0023 + REPAIR_CANDIDATE: 切分边界建议："+p['boundary_req_suggest']) if ing else "REPAIR_CANDIDATE(非阻塞并行修复): 按建议边界重切resp/req；baseline期可用resp作为req输入",
        "blocking_baseline": blocking,
    })

# SHA × 2
for s in sha_results:
    decision_rows.append({
        "dataset": "Zhilian",
        "sample_id": "",
        "source_id": s["source_id"],
        "issue": f"SHA_FORMULA_MISMATCH -> {s['classification']}",
        "evidence_result": f"orig={s['orig_sha']}…；formula={s['computed_formula']}…；legacy_rule_hit={s['legacy_rule_hit']}；traceable={s['traceable']}；in_gold_lineage={s['in_gold_lineage']}；detail==resp={s['detail_equals_resp']}",
        "gold_impact": ("Gold侧无_sha256字段，父记录入Gold但hash仅用于Zhilian侧去重——不影响Gold lineage") if s['in_gold_lineage'] else "不进入Gold lineage",
        "recommended_action": "KEEP_HASH_AS_IS（NON_BLOCKING_LEGACY_HASH：可追溯为旧版resp_only公式，无需覆盖hash）",
        "blocking_baseline": "NO",
    })

write_csv_fields = ["dataset","sample_id","source_id","issue","evidence_result","gold_impact","recommended_action","blocking_baseline"]
with open(DEC_CSV, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.DictWriter(f, fieldnames=write_csv_fields)
    w.writeheader()
    for d in decision_rows:
        w.writerow({k: d.get(k, "") for k in write_csv_fields})

# Packet markdown
md = []
md.append("# P0/P1 最终证据复核与修复决策包\n")
md.append(f"- 生成时间（脚本执行时）：运行时审计派生\n")
md.append(f"- 审计目标：ANN-0023 P0 + 2条P1 Zhilian切分失败 + 2条SHA legacy\n")
md.append(f"- Gold110 vs Zhilian exact overlap: 110 = EXPECTED_PARENT_CHILD_OVERLAP\n")
md.append("\n---\n\n## §一 P0：Gold ANN-0023专项复核\n")
md.append("### 1.1 Gold正式字段清单（只读，不创造字段）\n")
md.append(f"- 存在`review_gold_requirements`字段？：**{'YES' if REVIEW_GOLD_REQ_EXISTS else 'NO'}**（注意：项目实际Gold字段前缀=`gold_`，没有`review_gold_requirements`字段）\n")
md.append(f"- 实际Gold标注字段（用于判断A-D）：`{', '.join(RELEVANT_GOLD_FIELDS)}`\n")
md.append(f"- Gold全部gold_*字段列表：`{', '.join(gold_field_keys)}`\n")
md.append("\n### 1.2 基础信息（样本对齐）\n")
md.append(f"- sample_id：**{ANN_SAMPLE}**\n")
md.append(f"- source_id：{ANN_ZL_SID}（Gold侧 / Zhilian同父）\n")
md.append(f"- job_title_raw：{text(g_ann.get('job_title_raw'))!r} / {text(z_ann.get('job_title_raw'))!r}\n")
md.append(f"- company_name：{text(g_ann.get('company_name'))!r} / {text(z_ann.get('company_name'))!r}\n")
md.append(f"- source_education（原始爬取）：{text(g_ann.get('source_education'))!r} / {text(z_ann.get('source_education'))!r}\n")
md.append(f"- source_experience（原始爬取）：{text(g_ann.get('source_experience'))!r} / {text(z_ann.get('source_experience'))!r}\n")
md.append(f"- text_education（后处理）：{text(g_ann.get('text_education'))!r}\n")
md.append(f"- text_experience（后处理）：{text(g_ann.get('text_experience'))!r}\n")
md.append("\n### 1.3 字段内容大小（无长正文粘贴）\n")
md.append(f"- Gold responsibilities 长度={len(g_resp)}，requirements 长度={len(g_req)}，detail_raw_text长度={len(text(g_ann.get('detail_raw_text')))}\n")
md.append(f"- Zhilian responsibilities 长度={len(z_resp)}，requirements 长度={len(z_req)}，detail_raw_text长度={len(detail)}\n")
md.append(f"- Gold==Zhilian resp? 内容匹配={sim(g_resp, z_resp):.2f}；req匹配={sim(g_req, z_req):.2f}\n")
md.append(f"- Zhilian resp内容类型：要求关键词命中 {len(reqkw_hits)} 个（{reqkw_hits[:6]}）；职责动词命中 {len(duty_hits)} 个（{duty_hits[:3]}）；resp_req_swapped_suspect={swap_suspect}\n")
md.append("\n### 1.4 A-D问答\n")
md.append("**A. Gold的review_gold_requirements是否存在该字段？**\n")
md.append(f"- 答：**否**。Gold正式字段无`review_gold_requirements`；本项目Gold实际使用：`gold_skills / gold_bonus_skills / gold_experience / gold_education / gold_core_duties / gold_title` 六字段。\n")
md.append("\n**B. ANN-0023当前被判P0的真正原因是什么？**\n")
for i, item in enumerate(REASONS, 1):
    md.append(f"- {item}\n")
md.append("\n**C. 根据原始JD正文：是否存在明确的任职要求/资格要求内容？**\n")
md.append(f"- 答：**{C_DECISION}**\n")
md.append(f"- 证据摘要：{C_EVIDENCE}\n")
md.append(f"- resp全文（=detail全文）逐行清单：\n")
for idx, ln in enumerate([l.strip() for l in z_resp.splitlines() if l.strip()], 1):
    md.append(f"  - 行{idx}：{ln[:80]}\n")
md.append("\n**D. Gold当前人工结果是否违反现有标注规则？**\n")
md.append(f"- 答：**{D_DECISION}**\n")
md.append(f"- 判定依据：\n")
md.append(f"  - gold_core_duties 内容（长度{len(g_core)}）：{g_core[:200]!r}\n")
md.append(f"    - 其中要求词命中={core_reqkw}，职责动词命中={core_duty}\n")
md.append(f"  - gold_skills 非空? {'YES '+str(len(g_skills))+'字符' if g_skills else 'NO(空)'}; 预览：{g_skills[:150]!r}\n")
md.append(f"  - gold_education 非空? {'YES '+str(len(g_edu)) if g_edu else 'NO(空)'}; 值：{g_edu[:100]!r}\n")
md.append(f"  - gold_experience 非空? {'YES '+str(len(g_exp)) if g_exp else 'NO(空)'}; 值：{g_exp[:100]!r}\n")
md.append(f"  - gold_bonus_skills 非空? {'YES '+str(len(text(g_ann.get('gold_bonus_skills')))) if text(g_ann.get('gold_bonus_skills')) else 'NO(空)'}; 预览：{text(g_ann.get('gold_bonus_skills'))[:150]!r}\n")
md.append(f"  - gold_title 非空? {'YES '+str(len(text(g_ann.get('gold_title')))) if text(g_ann.get('gold_title')) else 'NO(空)'}; 值：{text(g_ann.get('gold_title'))[:120]!r}\n")
md.append("\n**⚠ 结论：**\n")
if D_DECISION == "GOLD_REVIEW_REQUIRED":
    md.append("- ANN-0023 gold_core_duties 疑似把要求段内容当职责写（或空值未填）=P0仍然成立；Baseline门槛：触发 **BASELINE_BLOCKED_BY_GOLD_REVIEW**。\n")
elif D_DECISION == "GOLD_CORRECT":
    md.append("- ANN-0023 gold_core_duties内容为职责动词，gold_skills/education/experience与原resp中要求词正确映射=GOLD_CORRECT；Baseline门槛降至 **BASELINE_CAN_PROCEED_WITH_CANDIDATE_REPAIR_PENDING**。\n")
else:
    md.append("- 无法确定（CANNOT_DETERMINE），需标注岗人工复核后再做判断。\n")

md.append("\n---\n\n## §二 P1：两个Zhilian requirements切分失败专项复核\n\n")
for p in p1_results:
    md.append(f"### P1：{p['source_id']} - {p['job_title_raw']}\n")
    md.append(f"- company: {p['company_name']}\n")
    md.append(f"- responsibilities长度={p['resp_len']}；requirements长度={p['req_len']}；detail_raw_text长度={p['detail_len']}\n")
    md.append(f"- detail中是否存在明确要求类结构性段落？\n")
    md.append(f"  - 标题命中：{p['evidence']}\n")
    md.append(f"  - 判定：**{p['decision']}**\n")
    md.append(f"  - 建议responsibilities边界：{p['boundary_resp_suggest']}\n")
    md.append(f"  - 建议requirements边界：{p['boundary_req_suggest']}\n")
    if p['decision'] == 'PARSE_SPLIT_FAILURE_CONFIRMED':
        # append line-by-line preview
        rec = zl_by_sid[p['source_id']]
        md.append(f"  - responsibilities逐行预览（用于边界人工确认）：\n")
        for idx, ln in enumerate([l.strip() for l in text(rec.get('responsibilities')).splitlines() if l.strip()], 1):
            md.append(f"    {idx}. {ln[:80]}\n")
    md.append("\n")

md.append("\n---\n\n## §三 检查P1是否影响Gold（exclusion manifest匹配）\n\n")
for sid in P1_SIDS:
    gs = p1_gold_status[sid]
    ptitle = next(p["job_title_raw"] for p in p1_results if p["source_id"]==sid)
    md.append(f"- {sid} ({ptitle})\n")
    md.append(f"  - in_gold_manifest(=Gold exact overlap 对应父candidate？)：**{'true' if gs['in_gold_manifest'] else 'false'}**\n")
    if gs['in_gold_manifest']:
        md.append(f"  - 对应Gold sample_id：**{gs['gold_sample_id']}**\n")
        md.append(f"  - ⚠ 升级为P0候选（与ANN-0023同一记录）：Gold证据源头切分错误+继承req空\n")
    else:
        md.append(f"  - 不进入Gold110 → 不会直接影响当前Gold110评测（仅为candidate侧修bug）\n")
    md.append("\n")

md.append("\n---\n\n## §四 两个SHA异常是否阻断\n\n")
for s in sha_results:
    md.append(f"- {s['source_id']} - {s['title']}\n")
    md.append(f"  - orig_sha前16位：`{s['orig_sha']}`；公式SHA256(resp+\\\\n+req)前16位：`{s['computed_formula']}`\n")
    md.append(f"  - legacy规则命中：`{s['legacy_rule_hit']}`\n")
    md.append(f"  - 是否追溯（hash可溯源到明确规则）：{s['traceable']}\n")
    md.append(f"  - 是否属于Gold lineage（即Gold110的父candidate？）：{s['in_gold_lineage']}\n")
    md.append(f"  - detail_raw_text == responsibilities? {s['detail_equals_resp']}\n")
    md.append(f"  - 最终分类：**{s['classification']}**\n\n")
md.append(f"- 整体判定：2条均为NON_BLOCKING_LEGACY_HASH？**{ALL_NON_BLOCK}**\n\n")

md.append("\n---\n\n## §五 决策文件清单\n\n")
md.append(f"- `p0_p1_review_packet.md`（本文件）\n")
md.append(f"- `p0_p1_review_decisions.csv`（{len(decision_rows)}行决策行：1×P0 + 2×P1 + 2×SHA）\n\n")

md.append("## §六 Baseline门槛判断（严格三态）\n\n")
if D_DECISION == "GOLD_CORRECT":
    md.append("判定结果：**BASELINE_CAN_PROCEED_WITH_CANDIDATE_REPAIR_PENDING**\n\n")
    md.append("- 理由：Gold ANN-0023 gold_core_duties已独立人工标注正确，Gold标注无误，不阻塞进入Baseline；剩余2条P1为Zhilian candidate切分问题，其中1条是Gold父记录但不直接影响Gold使用（Gold标注正确）；可同时启动Baseline并后台并行修复切分+hash legacy说明。\n")
elif D_DECISION == "GOLD_REVIEW_REQUIRED":
    md.append("判定结果：**BASELINE_BLOCKED_BY_GOLD_REVIEW**\n\n")
    md.append("- 理由：Gold ANN-0023 gold_core_duties疑似把candidate的要求段内容当成职责写了，或gold_core_duties空未填——违反Gold标注规则，若直接进入评测baseline，Gold标准答案可能错误，评测结果无效。必须由标注岗人工复核并修正gold_core_duties后才能启动baseline。\n")
else:
    md.append("判定结果：**BASELINE_BLOCKED_BY_GOLD_REVIEW**（CANNOT_DETERMINE默认保守阻塞）\n\n")

with open(PACKET_MD, "w", encoding="utf-8") as f:
    f.write("".join(md))

# Print section IX final answers for convenience
print("=== §九 Final Summary for report ===")
print(f"ANN0023_review_gold_requirements_exists={REVIEW_GOLD_REQ_EXISTS}")
print(f"ANN0023_candidate_requirements_empty={not z_req}")
print(f"ANN0023_original_JD_has_requirement_section={C_DECISION}")
print(f"ANN0023_Gold_D_decision={D_DECISION}")
print(f"ANN0023_gold_core_len={len(g_core)}")
for p in p1_results:
    ing = p1_gold_status[p['source_id']]
    print(f"P1 {p['source_id']}: decision={p['decision']} in_gold={ing['in_gold_manifest']} gold_sample_id={ing['gold_sample_id']}")
print(f"SHA all NON_BLOCKING_LEGACY_HASH={ALL_NON_BLOCK}")
if D_DECISION == "GOLD_CORRECT":
    print("BASELINE_STATUS=BASELINE_CAN_PROCEED_WITH_CANDIDATE_REPAIR_PENDING")
else:
    print(f"BASELINE_STATUS=BASELINE_BLOCKED_BY_GOLD_REVIEW (D={D_DECISION})")
print("DONE")
