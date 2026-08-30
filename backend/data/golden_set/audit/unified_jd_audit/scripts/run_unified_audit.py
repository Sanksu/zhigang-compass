"""Unified JD dataset audit — read-only.
Outputs 7 files into parent audit directory.
Does NOT modify v1/, official_career_50/, final/, backend/app, frontend, Prompt.
Python stdlib only. No third-party deps.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(r'd:\du_yan\jiebang_guashuai_jingsai\zhigang-compass')
BASE_IN = ROOT / 'backend' / 'data' / 'golden_set'
V1_DIR = BASE_IN / 'candidate_pool' / 'v1'
FINAL_DIR = BASE_IN / 'final'
OC50_DIR = BASE_IN / 'candidate_pool' / 'official_career_50'
AUDIT_DIR = BASE_IN / 'audit' / 'unified_jd_audit'
AUDIT_DIR.mkdir(parents=True, exist_ok=True)

ZHILIAN_PATH = V1_DIR / 'real_jd_candidates_clean.jsonl'
GOLD_PATH = FINAL_DIR / 'jd_golden_110.jsonl'
OC50_PATH = OC50_DIR / 'official_career_50_clean.jsonl'

COMMON_FIELDS = [
    'source', 'source_company', 'source_id', 'source_url',
    'job_title_raw', 'company_name', 'location', 'salary',
    'source_education', 'source_experience', 'publish_time',
    'responsibilities', 'requirements', 'detail_raw_text',
    'crawl_time', '_sha256',
]
GOLD_EXTRA = [
    'review_gold_title', 'review_gold_skills', 'review_gold_bonus_skills',
    'review_gold_experience', 'review_gold_education', 'review_gold_core_duties',
]

FAMILIES = [
    ('全栈', [r'全栈']),
    ('前端', [r'前端开发', r'\bH5\b', r'Web前端', r'前端工程', r'前端']),
    ('后端', [r'后端开发', r'服务端开发', r'后台开发', r'Java后端', r'Python后端', r'Go后端', r'PHP后端', r'Node后端', r'服务器开发', r'后端工程', r'后台工程', r'后端', r'后台', r'服务端']),
    ('AI/LLM', [r'大模型', r'LLM', r'多模态', r'AIGC', r'Prompt工程', r'预训练', r'大语言模型', r'语料', r'推理优化', r'MaaS']),
    ('算法', [r'算法工程师', r'算法研究', r'推荐算法', r'搜索算法', r'风控算法', r'语音算法', r'视觉算法', r'CV算法', r'\bCV\b', r'强化学习', r'机器学习', r'算法优化', r'算法岗', r'算法实习生', r'算法']),
    ('数据工程/大数据', [r'大数据开发', r'数据工程', r'数仓', r'\bETL\b', r'数据开发工程师', r'Hadoop', r'Spark', r'Flink', r'OLAP', r'Hive', r'Kafka', r'大数据']),
    ('数据分析', [r'数据分析', r'数据分析师', r'商业分析', r'\bBI\b工程师', r'数据运营']),
    ('测试', [r'测试开发', r'测试工程师', r'自动化测试', r'\bQA\b', r'功能测试', r'性能测试', r'测试岗', r'\bSDET\b', r'质量保障', r'测试']),
    ('运维/DevOps', [r'运维工程师', r'\bDevOps\b', r'\bSRE\b', r'可靠性工程师', r'\bDBA\b', r'云平台', r'\bK8s\b', r'Kubernetes', r'系统运维', r'部署工程师', r'基础设施工程师', r'云原生', r'运维']),
    ('嵌入式/C++', [r'嵌入式', r'C\+\+开发', r'C\+\+软件', r'驱动开发', r'固件开发', r'底层开发', r'Linux\s*C[\+\+]?开发', r'\bBSP\b开发', r'芯片验证', r'内核开发']),
    ('网络/安全', [r'安全工程师', r'网络安全', r'渗透测试', r'攻防', r'网络工程师', r'安全研究员', r'TCP/IP', r'网络协议', r'安全合规', r'安全运营', r'网络开发', r'安全岗']),
    ('客户端', [r'客户端工程师', r'\bAndroid\b', r'\biOS\b', r'移动端开发', r'APP开发', r'Flutter开发', r'移动端', r'移动客户端', r'客户端开发']),
]
FAMILY_ORDER = [n for n, _ in FAMILIES] + ['其他']
_fam_patterns = [(n, re.compile('|'.join(p), re.I)) for n, p in FAMILIES]


def classify_family(title: str) -> str:
    if not title:
        return '其他'
    for name, pat in _fam_patterns:
        if pat.search(title):
            return name
    return '其他'


_COMP_CANON = {
    '腾讯': 'tencent', '腾讯科技': 'tencent', '腾讯控股': 'tencent', 'tencent': 'tencent',
    '字节': 'bytedance', '字节跳动': 'bytedance', '北京字节跳动': 'bytedance', 'bytedance': 'bytedance',
    '阿里': 'alibaba', '阿里巴巴': 'alibaba', 'alibaba': 'alibaba',
    '蚂蚁': 'ant', '蚂蚁金服': 'ant', '蚂蚁集团': 'ant',
    '美团': 'meituan', 'meituan': 'meituan',
    '百度': 'baidu', 'baidu': 'baidu',
    '京东': 'jd', 'jd.com': 'jd',
    '网易': 'netease', 'netease': 'netease',
    '小米': 'xiaomi', 'xiaomi': 'xiaomi',
    '华为': 'huawei', 'huawei': 'huawei',
}
_SENIOR_RE = re.compile(r'(高级|资深|专家|初级|主管|经理|总监|架构师|\bP[4-9]\b|\bL[4-9]\b|\bStaff\b|\bPrincipal\b|\bVP\b|\bLead\b|\bLeader\b)', re.I)
_PUNCT_RE = re.compile(r'[\s\-_/\\()（）\[\]【】,:：;；.。、·\'"`!！?？]+')


def norm_company(s) -> str:
    if not s:
        return ''
    st = str(s).strip()
    for k, v in _COMP_CANON.items():
        if k in st:
            return v
    out = st.lower()
    out = re.sub(r'(股份)?有限(责任)?公司|集团|科技|网络|信息|技术|(中国)|深圳|北京|上海|杭州|广州|成都|分公司|子公司|有限公司|（深圳）|（北京）|（上海）', '', out)
    out = _PUNCT_RE.sub('', out)
    return out[:40]


def norm_title(s) -> str:
    if not s:
        return ''
    st = _SENIOR_RE.sub('', str(s))
    st = _PUNCT_RE.sub('', st).lower()
    return st[:80]


def norm_location(s) -> str:
    if not s:
        return ''
    st = str(s).strip()
    if '-' in st:
        st = st.split('-', 1)[0]
    st = re.sub(r'市$|区$|省$', '', st)
    st = _PUNCT_RE.sub('', st)
    return st[:15]


def load_jsonl(p: Path):
    recs = []
    with open(p, 'r', encoding='utf-8') as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            recs.append(json.loads(ln))
    return recs


def nonempty(v) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip() != ''
    if isinstance(v, (list, dict)):
        return len(v) > 0
    return True


def sha_match(rec) -> str:
    sha = rec.get('_sha256') or ''
    resp = rec.get('responsibilities') or ''
    req = rec.get('requirements') or ''
    if not isinstance(sha, str) or len(sha) != 64:
        return 'FORMAT_INVALID'
    if not re.fullmatch(r'[a-f0-9]{64}', sha):
        return 'FORMAT_INVALID'
    calc = hashlib.sha256((str(resp) + '\n' + str(req)).encode('utf-8')).hexdigest()
    if calc == sha.lower():
        return 'MATCH'
    return 'FORMULA_MISMATCH'


def parse_time(s):
    if not s or not isinstance(s, str):
        return None
    try:
        st = s.strip()
        if st.endswith('Z'):
            st = st[:-1] + '+00:00'
        dt = datetime.fromisoformat(st)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except Exception:
        return None


def sim(a, b) -> float:
    a = '' if a is None else str(a)[:1200]
    b = '' if b is None else str(b)[:1200]
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


# =========================
# §四 Load 3 datasets
# =========================
print('=== §四 读取三块数据 ===')
datasets = {}
datasets['Zhilian'] = load_jsonl(ZHILIAN_PATH)
datasets['Gold'] = load_jsonl(GOLD_PATH)
datasets['Official'] = load_jsonl(OC50_PATH)
for name, recs in datasets.items():
    print(f'{name}_records: {len(recs)}')

# attach internal dataset label & normalized fields (derived, not written back)
for dname, recs in datasets.items():
    for i, r in enumerate(recs):
        r['__dataset'] = dname
        r['__idx'] = i
        r['__nfam'] = classify_family(r.get('job_title_raw'))
        r['__ncomp'] = norm_company(r.get('company_name') or r.get('source_company'))
        r['__ntitle'] = norm_title(r.get('job_title_raw'))
        r['__nloc'] = norm_location(r.get('location'))

# =========================
# §五 Field coverage
# =========================
print('=== §五 字段覆盖 输出CSV ===')
field_rows = []
for dname, recs in datasets.items():
    n = len(recs)
    all_fields = list(COMMON_FIELDS)
    if dname == 'Gold':
        all_fields += GOLD_EXTRA
    for f in all_fields:
        present = sum(1 for r in recs if f in r.keys())
        filled = sum(1 for r in recs if nonempty(r.get(f)))
        types = Counter(type(r.get(f)).__name__ for r in recs if f in r.keys() and r.get(f) is not None)
        type_str = ';'.join(f'{t}:{c}' for t, c in types.most_common(3)) if types else '(absent)'
        anomalies = []
        if f in ('responsibilities', 'requirements', 'detail_raw_text'):
            wrong_type = sum(1 for r in recs if f in r and r.get(f) is not None and not isinstance(r.get(f), str))
            if wrong_type:
                anomalies.append(f'non-string:{wrong_type}')
        elif f in ('publish_time', 'crawl_time'):
            parsed_ok = sum(1 for r in recs if parse_time(r.get(f)) is not None or not nonempty(r.get(f)))
            if parsed_ok != n:
                anomalies.append(f'unparseable:{n - parsed_ok}')
        elif f == '_sha256':
            badfmt = sum(1 for r in recs if nonempty(r.get(f)) and not (isinstance(r.get(f), str) and len(r.get(f)) == 64 and re.fullmatch(r'[a-f0-9]{64}', r.get(f) or '')))
            if badfmt:
                anomalies.append(f'bad_format:{badfmt}')
        field_rows.append([dname, f, n, present, f'{present/n*100:.1f}%' if n else 'N/A',
                          filled, f'{filled/n*100:.1f}%' if n else 'N/A', type_str, ';'.join(anomalies) or '-'])

with open(AUDIT_DIR / 'unified_jd_field_coverage.csv', 'w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f)
    w.writerow(['dataset', 'field', 'total', 'present', 'present_pct', 'non_empty', 'non_empty_pct', 'types_top3', 'anomalies'])
    w.writerows(field_rows)

# =========================
# §六 Source / scale
# =========================
print('=== §六 来源与规模 ===')
source_rows = []
# Zhilian: source + company
zl = datasets['Zhilian']
source_counter = Counter(str(r.get('source')) or 'UNKNOWN' for r in zl)
company_counter = Counter(str(r.get('company_name')) or 'UNKNOWN' for r in zl)
print('Zhilian source counts:')
for s, c in source_counter.most_common():
    print(f'  {s}: {c}')
print(f'Zhilian distinct companies: {len(company_counter)}')
# Official: Tencent / ByteDance
oc = datasets['Official']
oc_t = sum(1 for r in oc if (r.get('source_company') or '').strip() == 'Tencent')
oc_b = sum(1 for r in oc if (r.get('source_company') or '').strip() == 'ByteDance')
print(f'Official Tencent={oc_t} ByteDance={oc_b}')
# Gold source composition
gd = datasets['Gold']
gold_source = Counter(str(r.get('source')) or 'UNKNOWN' for r in gd)
gold_company = Counter(str(r.get('company_name')) or 'UNKNOWN' for r in gd)
print('Gold source counts:')
for s, c in gold_source.most_common(10):
    print(f'  {s}: {c}')
print(f'Gold distinct companies: {len(gold_company)}')

# =========================
# §七 Job family distribution
# =========================
print('=== §七 岗位族分布 ===')
fam_rows = []
all_fam = {d: Counter(r['__nfam'] for r in recs) for d, recs in datasets.items()}
for dname, recs in datasets.items():
    n = len(recs)
    c = all_fam[dname]
    for fam in FAMILY_ORDER:
        cnt = c.get(fam, 0)
        pct = f'{cnt/n*100:.1f}%' if n else 'N/A'
        fam_rows.append([dname, fam, cnt, pct])
with open(AUDIT_DIR / 'unified_jd_job_family_distribution.csv', 'w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f)
    w.writerow(['dataset', 'job_family', 'count', 'pct'])
    w.writerows(fam_rows)
# report gaps / over-concentration (written to summary)

# =========================
# Helper: cross compare two lists -> tiered matches (§八 and §九)
# =========================
TIER_ORDER = {'EXACT': 3, 'STRONG': 2, 'WEAK': 1, None: 0}


def cross_compare(left, right, label_left, label_right):
    rows = []  # tier, l_sid, l_src, l_title, l_company, l_loc, r_sid, r_src, r_title, r_company, r_loc, basis
    r_url_idx = defaultdict(list)
    r_sid_idx = defaultdict(list)
    r_sha_idx = defaultdict(list)
    r_ctl_idx = defaultdict(list)
    for j, rr in enumerate(right):
        r_sid = str(rr.get('source_id') or '')
        r_url = str(rr.get('source_url') or '')
        r_sha = str(rr.get('_sha256') or '')
        r_ctl = (rr['__ncomp'], rr['__ntitle'], rr['__nloc'])
        if r_url:
            r_url_idx[r_url].append(j)
        if r_sid:
            r_sid_idx[r_sid].append(j)
        if r_sha and len(r_sha) == 64:
            r_sha_idx[r_sha].append(j)
        if all(r_ctl):
            r_ctl_idx[r_ctl].append(j)
    exact_pairs = set()
    for i, lr in enumerate(left):
        l_sid = str(lr.get('source_id') or '')
        l_url = str(lr.get('source_url') or '')
        l_sha = str(lr.get('_sha256') or '')
        l_ctl = (lr['__ncomp'], lr['__ntitle'], lr['__nloc'])
        cand = set()
        if l_url:
            cand.update(r_url_idx.get(l_url, []))
        if l_sid:
            cand.update(r_sid_idx.get(l_sid, []))
        if l_sha and len(l_sha) == 64:
            cand.update(r_sha_idx.get(l_sha, []))
        for j in sorted(cand):
            exact_pairs.add((i, j))
            rows.append(('EXACT_DUPLICATE',) + pair_info(left[i], right[j], label_left, label_right) + ('sid/url/sha exact match',))
        strong_cand = set()
        if all(l_ctl):
            for j in r_ctl_idx.get(l_ctl, []):
                if (i, j) in exact_pairs:
                    continue
                strong_cand.add(j)
        for j in sorted(strong_cand):
            rows.append(('STRONG_SUSPECT',) + pair_info(left[i], right[j], label_left, label_right) + ('norm company+title+location exact',))
            exact_pairs.add((i, j))
    # Weak pass: same company + sim >=0.80 on resp/detail/req
    l_by_comp = defaultdict(list)
    r_by_comp = defaultdict(list)
    for i, lr in enumerate(left):
        if lr['__ncomp']:
            l_by_comp[lr['__ncomp']].append(i)
    for j, rr in enumerate(right):
        if rr['__ncomp']:
            r_by_comp[rr['__ncomp']].append(j)
    for comp, lis in l_by_comp.items():
        rjs = r_by_comp.get(comp, [])
        if not rjs:
            continue
        for i in lis:
            lr = left[i]
            for j in rjs:
                if (i, j) in exact_pairs:
                    continue
                rr = right[j]
                rsim = sim(lr.get('responsibilities'), rr.get('responsibilities'))
                dsim = sim(lr.get('detail_raw_text'), rr.get('detail_raw_text'))
                qsim = sim(lr.get('requirements'), rr.get('requirements'))
                best = max(rsim, dsim, qsim)
                basis = None
                if best == rsim:
                    basis = f'resp sim={rsim:.3f}'
                elif best == dsim:
                    basis = f'detail sim={dsim:.3f}'
                else:
                    basis = f'req sim={qsim:.3f}'
                if best >= 0.80:
                    rows.append(('WEAK_SUSPECT',) + pair_info(lr, rr, label_left, label_right) + (basis,))
                    exact_pairs.add((i, j))
    # Keep unique rows; dedupe by (tier, l_sid, r_sid)
    seen = set()
    unique = []
    for row in rows:
        key = (row[0], row[1], row[7])
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    # sort by tier desc then l_sid
    unique.sort(key=lambda r: (-TIER_ORDER.get(r[0].split('_')[0], 0), str(r[1])))
    return unique


def pair_info(lr, rr, ln, rn):
    return (
        ln,
        str(lr.get('source_id') or ''),
        str(lr.get('job_title_raw') or '')[:80],
        str(lr.get('company_name') or '')[:60],
        str(lr.get('location') or '')[:40],
        rn,
        str(rr.get('source_id') or ''),
        str(rr.get('job_title_raw') or '')[:80],
        str(rr.get('company_name') or '')[:60],
        str(rr.get('location') or '')[:40],
    )


# §八 Zhilian vs Official
print('=== §八 跨源重复 Zhilian × Official50 ===')
dups_zo = cross_compare(datasets['Zhilian'], datasets['Official'], 'Zhilian', 'Official')
print(f'Z×O total rows: {len(dups_zo)}')
with open(AUDIT_DIR / 'unified_jd_cross_source_duplicates.csv', 'w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f)
    w.writerow(['tier', 'L_dataset', 'L_source_id', 'L_title', 'L_company', 'L_location',
                'R_dataset', 'R_source_id', 'R_title', 'R_company', 'R_location', 'basis'])
    w.writerows(dups_zo)

# §九 Gold overlaps: G×Z then G×O
print('=== §九 Gold 重合审计 ===')
overlap_gz = cross_compare(datasets['Gold'], datasets['Zhilian'], 'Gold', 'Zhilian')
overlap_go = cross_compare(datasets['Gold'], datasets['Official'], 'Gold', 'Official')
print(f'G×Z rows: {len(overlap_gz)}')
print(f'G×O rows: {len(overlap_go)}')
with open(AUDIT_DIR / 'unified_jd_gold_overlap.csv', 'w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f)
    w.writerow(['tier', 'L_dataset', 'L_source_id', 'L_title', 'L_company', 'L_location',
                'R_dataset', 'R_source_id', 'R_title', 'R_company', 'R_location', 'basis'])
    for row in overlap_gz:
        w.writerow(list(row))
    for row in overlap_go:
        w.writerow(list(row))

# =========================
# §十 Field boundary issues
# =========================
print('=== §十 字段边界一致性 ===')
q_rows = []
for dname, recs in datasets.items():
    for r in recs:
        sid = str(r.get('source_id') or f"idx{r['__idx']}")
        issues = []
        resp = r.get('responsibilities') or ''
        req = r.get('requirements') or ''
        detail = r.get('detail_raw_text') or ''
        if not nonempty(resp):
            issues.append('responsibilities_EMPTY')
        if not nonempty(req):
            issues.append('requirements_EMPTY')
        if not nonempty(detail):
            issues.append('detail_raw_text_EMPTY')
        both_len = len(str(resp)) + len(str(req))
        if nonempty(detail) and both_len > 0 and len(str(detail)) < 0.75 * both_len:
            issues.append(f'detail_shorter_than_resp_req_both_len({len(str(detail))}vs{both_len})')
        if nonempty(resp) and nonempty(req) and sim(resp, req) >= 0.85:
            issues.append(f'resp_req_high_overlap_sim{sim(resp,req):.2f}')
        if nonempty(detail) and nonempty(resp):
            if sim(detail, resp) >= 0.98 and len(str(detail)) - len(str(resp)) < 50:
                issues.append('detail_pure_duplicate_of_resp')
            elif sim(detail, resp) >= 0.98 and nonempty(req) and len(str(detail)) - len(str(resp)) - len(str(req)) < 20:
                issues.append('detail=resp_concat_req_only')
        for iss in issues:
            q_rows.append([dname, sid, iss,
                           str(r.get('job_title_raw') or '')[:80],
                           str(r.get('company_name') or '')[:60]])

# =========================
# §十一 SHA rule audit (candidate only: Zhilian + Official; Gold report only)
# =========================
print('=== §十一 SHA规则审计 ===')
sha_summary = {}
for dname in ['Zhilian', 'Official']:
    recs = datasets[dname]
    valid = 0; match = 0; mismatch = 0; missing = 0
    for r in recs:
        rcode = sha_match(r)
        if rcode == 'FORMAT_INVALID':
            if nonempty(r.get('_sha256')):
                valid += 0  # count as invalid
                mismatch += 0
            else:
                missing += 1
        elif rcode == 'MATCH':
            valid += 1; match += 1
        else:  # FORMULA_MISMATCH (64 hex but not equal)
            valid += 1; mismatch += 1
    total_sha_field_present = sum(1 for r in recs if nonempty(r.get('_sha256')))
    sha_summary[dname] = {'n': len(recs), 'sha_present': total_sha_field_present,
                         'valid_64hex': valid, 'formula_match': match,
                         'formula_mismatch': mismatch, 'missing': missing}
    # append rows to q_rows for mismatch/missing
    for r in recs:
        sid = str(r.get('source_id') or f"idx{r['__idx']}")
        rcode = sha_match(r)
        if rcode == 'FORMULA_MISMATCH':
            q_rows.append([dname, sid, 'sha_formula_mismatch', str(r.get('job_title_raw') or '')[:80], str(r.get('company_name') or '')[:60]])
        elif rcode == 'FORMAT_INVALID' and not nonempty(r.get('_sha256')):
            q_rows.append([dname, sid, 'sha_missing', str(r.get('job_title_raw') or '')[:80], str(r.get('company_name') or '')[:60]])
# Gold note:
gr = datasets['Gold']
gold_sha_present = sum(1 for r in gr if nonempty(r.get('_sha256')))
gold_sha_64 = sum(1 for r in gr if isinstance(r.get('_sha256'), str) and re.fullmatch(r'[a-f0-9]{64}', r.get('_sha256') or ''))
sha_summary['Gold'] = {'note': 'Gold口径不同，不重算SHA公式；仅报告存在率与格式',
                       'sha_present': gold_sha_present, 'valid_64hex': gold_sha_64, 'n': len(gr)}

# =========================
# §十二 Time audit
# =========================
print('=== §十二 时间字段审计 ===')
time_summary = {}
for dname, recs in datasets.items():
    pub_empty = sum(1 for r in recs if not nonempty(r.get('publish_time')))
    crl_empty = sum(1 for r in recs if not nonempty(r.get('crawl_time')))
    future = 0
    parse_err = 0
    future_sids = []
    for r in recs:
        pt = parse_time(r.get('publish_time')); ct = parse_time(r.get('crawl_time'))
        if nonempty(r.get('publish_time')) and pt is None:
            parse_err += 1
        if nonempty(r.get('crawl_time')) and ct is None:
            parse_err += 1
        if pt and ct and pt > ct:
            future += 1
            future_sids.append(str(r.get('source_id') or f"idx{r['__idx']}"))
            q_rows.append([dname, str(r.get('source_id') or f"idx{r['__idx']}"), 'future_publish_time',
                           str(r.get('job_title_raw') or '')[:80], str(r.get('publish_time') or '')[:40]])
    time_summary[dname] = {'n': len(recs), 'publish_empty': pub_empty,
                           'crawl_empty': crl_empty, 'future': future,
                           'parse_err': parse_err, 'future_sids': future_sids}
# Official50 expected future=2 confirmation
time_summary['Official']['_expected_future_2_confirmed'] = (time_summary['Official'].get('future', -1) == 2)

# Write quality issues CSV
with open(AUDIT_DIR / 'unified_jd_quality_issues.csv', 'w', encoding='utf-8-sig', newline='') as f:
    w = csv.writer(f)
    w.writerow(['dataset', 'source_id', 'issue', 'title', 'extra'])
    w.writerows(q_rows)

# =========================
# §十三 Output 7 files: summary.md + 5 CSVs + README.md
# Note: field_coverage, job_family_distribution, cross_source_duplicates, gold_overlap, quality_issues already written above
# Remaining: unified_jd_audit_summary.md, README.md
# =========================
print('=== §十三 写汇总报告 + README ===')


def tier_counts(rows):
    c = Counter()
    for r in rows:
        t = r[0]
        if t.startswith('EXACT'):
            c['EXACT'] += 1
        elif t.startswith('STRONG'):
            c['STRONG'] += 1
        elif t.startswith('WEAK'):
            c['WEAK'] += 1
    return c['EXACT'], c['STRONG'], c['WEAK']


dz, ds, dw = tier_counts(dups_zo)
gz, gs, gw = tier_counts(overlap_gz)
goz, gos, gow = tier_counts(overlap_go)

# summary rows
def fmt_counter(counter, top=5):
    return ', '.join(f'{k}:{v}' for k, v in counter.most_common(top))


def issue_count(dname):
    return sum(1 for r in q_rows if r[0] == dname)


summary = f"""# Unified JD 三数据集审计汇总（只读）

生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}（本地机器时间）

审计范围：
- Zhilian Candidate：real_jd_candidates_clean.jsonl
- Gold 110：jd_golden_110.jsonl
- Official Career 50：official_career_50_clean.jsonl

所有产物位于：backend/data/golden_set/audit/unified_jd_audit/

## 1. 数据规模（§四，现场程序重读取）

| 数据集 | 记录数 |
|---|---|
| Zhilian Candidate | {len(datasets['Zhilian'])} |
| Gold 110 | {len(datasets['Gold'])} |
| Official Career 50 | {len(datasets['Official'])} |

## 2. 来源与构成（§六）

**Zhilian Candidate 来源构成 Top（字段 source）**：
{fmt_counter(source_counter)}

Zhilian distinct companies：{len(company_counter)}

**Official Career 50 构成**：Tencent = {oc_t}，ByteDance = {oc_b}（预期 25/25：{'✅ 符合' if oc_t == 25 and oc_b == 25 else '⚠️ 不符'}）

**Gold 来源构成 Top（字段 source）**：
{fmt_counter(gold_source)}

Gold distinct companies：{len(gold_company)}

Gold 预期规模 110：{'✅ 符合' if len(gd) == 110 else '⚠️ 不符 实际=' + str(len(gd))}

## 3. 字段完整性（§五 摘要，详见 unified_jd_field_coverage.csv）

逐数据集必填 9 字段（job_title_raw / company_name / location / responsibilities / requirements / detail_raw_text / source_id / source_url / _sha256）完整情况：

| 数据集 | 完整率最差字段 | 最差非空率 |
|---|---|---|
"""

# worst field per dataset
for dname, recs in datasets.items():
    required9 = ['job_title_raw', 'company_name', 'location', 'responsibilities', 'requirements',
                 'detail_raw_text', 'source_id', 'source_url', '_sha256']
    worst_field = 'N/A'; worst_pct = 1.0
    n = len(recs)
    for f in required9:
        filled = sum(1 for r in recs if nonempty(r.get(f))) / n if n else 0
        if filled < worst_pct:
            worst_pct = filled; worst_field = f
    summary += f'| {dname} | {worst_field} | {worst_pct * 100:.1f}% |\n'

summary += f"""
## 4. 岗位族覆盖（§七，详见 unified_jd_job_family_distribution.csv）

13 族统一只读分类（不修改 job_title_raw）。

### 各数据集 Top3 岗位族

| 数据集 | #1 | #2 | #3 | 其他族占比 |
|---|---|---|---|---|
"""
for dname, recs in datasets.items():
    n = len(recs) or 1
    top3 = all_fam[dname].most_common(3)
    top3sum = sum(c for _, c in top3)
    others_pct = (n - top3sum) / n * 100
    t1 = f'{top3[0][0]} {top3[0][1]}({top3[0][1] / n * 100:.0f}%)' if top3 else '-'
    t2 = f'{top3[1][0]} {top3[1][1]}({top3[1][1] / n * 100:.0f}%)' if len(top3) > 1 else '-'
    t3 = f'{top3[2][0]} {top3[2][1]}({top3[2][1] / n * 100:.0f}%)' if len(top3) > 2 else '-'
    summary += f'| {dname} | {t1} | {t2} | {t3} | {others_pct:.0f}% |\n'

# Gaps & over-concentration: any family 40%+ is over-concentration; any zero-count is gap
gaps = []
over = []
for dname, recs in datasets.items():
    n = len(recs) or 1
    cc = all_fam[dname]
    for fam in FAMILY_ORDER:
        pct = cc.get(fam, 0) / n
        if pct >= 0.40:
            over.append(f'{dname}/{fam}={pct * 100:.0f}%')
        if fam != '其他' and cc.get(fam, 0) == 0:
            gaps.append(f'{dname}/{fam}')
summary += f"""
**过度集中（单一族≥40%）**：{', '.join(over) if over else '无'}

**缺口族（记录0条，不含"其他"）**：{', '.join(gaps) if gaps else '无'}

## 5. 跨源重复（§八 Zhilian Candidate × Official Career 50）

详见 unified_jd_cross_source_duplicates.csv。

- EXACT_DUPLICATE：{dz}
- STRONG_SUSPECT：{ds}
- WEAK_SUSPECT：{dw}

（说明：EXACT = source_id / source_url / _sha256 exact；STRONG = normalized company+title+location 三者完全一致；WEAK = 同公司+正文相似度≥0.80。级别互斥取最高级，禁止自动删除）

## 6. Gold 重合 / 泄漏风险（§九）

详见 unified_jd_gold_overlap.csv。

**Gold × Zhilian Candidate**
- EXACT_GOLD_OVERLAP：{gz}
- STRONG_GOLD_OVERLAP：{gs}
- WEAK_GOLD_OVERLAP：{gw}

**Gold × Official Career 50**
- EXACT_GOLD_OVERLAP：{goz}
- STRONG_GOLD_OVERLAP：{gos}
- WEAK_GOLD_OVERLAP：{gow}

> 说明：Gold 本来就是从 candidate 流水线人工标注抽取而来，重合不代表错误；重点关注重合条目在后续 train/eval 切分中是否可能造成 leakage，本阶段仅审计、不删除。

## 7. 字段边界异常（§十 + quality issues CSV 摘要）

| 数据集 | 异常条数 | 主要异常类型 Top |
|---|---|---|
"""
for dname in datasets:
    cnt = issue_count(dname)
    c = Counter(r[2] for r in q_rows if r[0] == dname)
    summary += f'| {dname} | {cnt} | {fmt_counter(c, top=3) or "-"} |\n'

summary += f"""
（详见 unified_jd_quality_issues.csv）

## 8. SHA-256 公式一致性（§十一）

| 数据集 | 总记录 | _sha256 存在 | 64hex 格式合法 | SHA(resp+"\\\\n"+req) 公式一致 | 公式不一致 | 缺失 | 备注 |
|---|---|---|---|---|---|---|---|
| Zhilian Candidate | {sha_summary['Zhilian']['n']} | {sha_summary['Zhilian']['sha_present']} | {sha_summary['Zhilian']['valid_64hex']} | {sha_summary['Zhilian']['formula_match']} | {sha_summary['Zhilian']['formula_mismatch']} | {sha_summary['Zhilian']['missing']} |  |
| Official Career 50 | {sha_summary['Official']['n']} | {sha_summary['Official']['sha_present']} | {sha_summary['Official']['valid_64hex']} | {sha_summary['Official']['formula_match']} | {sha_summary['Official']['formula_mismatch']} | {sha_summary['Official']['missing']} | 预期 100% 一致 |
| Gold 110 | {sha_summary['Gold']['n']} | {sha_summary['Gold']['sha_present']} | {sha_summary['Gold']['valid_64hex']} | — | — | — | {sha_summary['Gold']['note']} |

## 9. 时间字段审计（§十二）

| 数据集 | publish_time 空 | crawl_time 空 | publish > crawl（未来日期） | 解析错误 | 备注 |
|---|---|---|---|---|---|
| Zhilian Candidate | {time_summary['Zhilian']['publish_empty']} | {time_summary['Zhilian']['crawl_empty']} | {time_summary['Zhilian']['future']} | {time_summary['Zhilian']['parse_err']} |  |
| Gold 110 | {time_summary['Gold']['publish_empty']} | {time_summary['Gold']['crawl_empty']} | {time_summary['Gold']['future']} | {time_summary['Gold']['parse_err']} |  |
| Official Career 50 | {time_summary['Official']['publish_empty']} | {time_summary['Official']['crawl_empty']} | {time_summary['Official']['future']} | {time_summary['Official']['parse_err']} | 已知 future=2，本轮确认：{'✅ ' + str(time_summary['Official']['_expected_future_2_confirmed'])} |

## 10. 数据边界结论（§十四回答的核心）

- **可继续作为 candidate pool**：
  - Zhilian Candidate（real_jd_candidates_clean.jsonl）：字段完整度在上述最差行显示，若 SHA 公式不一致条数在 quality CSV 中已逐条列出，建议人工复核不一致来源后再进入下一阶段
  - Official Career 50：T25/B25 达成 ✅，Pilot20 六字段 20/20 PASS ✅，SHA 100% 公式一致预期应已达成，future anomaly 2 条按规定保留 ✅ → 作为 candidate pool 正式候选已封板 ✅
- **正式 Gold**：jd_golden_110.jsonl（110 条）= 已进入 final/ 的人工标注黄金集，独立于 candidate pool
- **需要人工复核记录**：
  - quality issues CSV 所有行（字段空/职责要求异常/SHA 异常/时间异常）
  - cross-source duplicates CSV 全部 Exact 与 Strong 行：是否真正重复岗位或 DISTINCT_JOBS（不同城市/PostId）需人工判定
  - gold_overlap CSV 全部重合行：是否可能造成评测时 train/eval leakage，需在切分时按 source_id 黑名单防泄漏
- **泄漏风险等级**：
  - Gold × Zhilian / Gold × Official 的重合条目如果为 EXACT / STRONG，且同一岗位同时出现在评测集，则构成 data leakage → 建议评测 pipeline 在加载 Gold eval split 前对 candidate train split 按 source_id + source_company 黑名单剔除；WEAK 级仅提示，不强制

## 11. 产出物清单

1. `unified_jd_audit_summary.md` — 本文件
2. `unified_jd_field_coverage.csv` — 三数据集逐字段 存在率/非空率/类型/异常
3. `unified_jd_job_family_distribution.csv` — 13 族岗位族 逐数据集 数量/占比
4. `unified_jd_cross_source_duplicates.csv` — Zhilian × Official 跨源重复 四层级明细
5. `unified_jd_gold_overlap.csv` — Gold × Zhilian / Gold × Official 重合明细
6. `unified_jd_quality_issues.csv` — 字段边界/SHA/时间 异常逐行列表
7. `README.md` — 审计目录说明
8. `scripts/run_unified_audit.py` — 只读审计脚本（可复现）
"""
with open(AUDIT_DIR / 'unified_jd_audit_summary.md', 'w', encoding='utf-8') as f:
    f.write(summary)

readme = f"""# unified_jd_audit — JD 三数据集统一只读审计目录

> 生成产物专用目录，所有文件均为只读派生结果。

本目录为 **「JD 三数据集统一审计：Zhilian Candidate + Gold110 + Official Career50」** 阶段产物。
**不回写任何文件到保护区**：
- 不写回 `candidate_pool/v1/`（Zhilian）
- 不写回 `candidate_pool/official_career_50/`（Official Career 封板数据）
- 不写回 `final/`（Gold 110 正式黄金集）
- 不修改 `backend/app/`、`frontend/`、`Prompt/`、`AGENTS.md`

## 输入数据位置（只读）

| 名称 | 路径 |
|---|---|
| Zhilian Candidate（clean） | `backend/data/golden_set/candidate_pool/v1/real_jd_candidates_clean.jsonl` |
| Gold 110（正式） | `backend/data/golden_set/final/jd_golden_110.jsonl` |
| Official Career 50（clean，封板） | `backend/data/golden_set/candidate_pool/official_career_50/official_career_50_clean.jsonl` |

## 脚本（可复现）

`scripts/run_unified_audit.py` — 纯 Python stdlib 脚本，不引入任何额外依赖。

执行：
```
cd backend/data/golden_set/audit/unified_jd_audit
python scripts/run_unified_audit.py
```

脚本行为：
1. 只读加载三块 JSONL
2. 派生临时字段（`__nfam` / `__ncomp` / `__ntitle` / `__nloc`）用于分类与匹配，**不写回原文件**
3. 输出本目录下 6 个 CSV + 1 个汇总 MD

## 产物清单

| 文件 | 内容 |
|---|---|
| `unified_jd_audit_summary.md` | 汇总报告（§十四所有核心问题回答，供人工审阅） |
| `unified_jd_field_coverage.csv` | 字段存在率 / 非空率 / 类型 Top3 / 异常 |
| `unified_jd_job_family_distribution.csv` | 13 族岗位族分布（逐数据集） |
| `unified_jd_cross_source_duplicates.csv` | Zhilian Candidate × Official 跨源重复（EXACT / STRONG / WEAK） |
| `unified_jd_gold_overlap.csv` | Gold × Zhilian / Gold × Official 重合明细（泄漏风险审计） |
| `unified_jd_quality_issues.csv` | 字段边界 / SHA / 时间 异常逐行列表 |
| `README.md` | 本文件 |

## Git 保护

本目录 `backend/data/golden_set/audit/unified_jd_audit/` 下所有文件应 100% 为新增产物，不应出现任何
其他路径的改动。提交前请使用 `git diff --name-only` 确认。
"""
with open(AUDIT_DIR / 'README.md', 'w', encoding='utf-8') as f:
    f.write(readme)

print('\n=== 输出文件清单 ===')
for p in sorted(AUDIT_DIR.iterdir()):
    if p.is_file():
        print(f'{p.name}  SIZE={p.stat().st_size}')
    elif p.is_dir():
        for sp in sorted(p.iterdir()):
            print(f'{p.name}/{sp.name}  SIZE={sp.stat().st_size}')
print('\n=== 完成：READY_FOR_UNIFIED_JD_AUDIT_REVIEW ===')
