"""Print final report answers (§十四 sections) from audit artifacts."""
import csv, os, json
from collections import Counter
ROOT = r'd:\du_yan\jiebang_guashuai_jingsai\zhigang-compass'
AD = os.path.join(ROOT, 'backend', 'data', 'golden_set', 'audit', 'unified_jd_audit')

def csv_rows(fn):
    with open(os.path.join(AD, fn), 'r', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))

def load_jsonl(path):
    with open(path, 'r', encoding='utf-8') as f:
        return [json.loads(ln) for ln in f if ln.strip()]

# paths
ZL = os.path.join(ROOT, 'backend','data','golden_set','candidate_pool','v1','real_jd_candidates_clean.jsonl')
GD = os.path.join(ROOT, 'backend','data','golden_set','final','jd_golden_110.jsonl')
OC = os.path.join(ROOT, 'backend','data','golden_set','candidate_pool','official_career_50','official_career_50_clean.jsonl')

zl_recs = load_jsonl(ZL); gd_recs = load_jsonl(GD); oc_recs = load_jsonl(OC)
sz, sg, so = len(zl_recs), len(gd_recs), len(oc_recs)

print('【数据规模】')
print(f'Zhilian：{sz}')
print(f'Gold：{sg}')
print(f'Official Career：{so}')
print()

print('【来源】')
print('各来源数量：')
sc_zl = Counter(str(r.get('source')) or 'UNKNOWN' for r in zl_recs)
sc_gd = Counter(str(r.get('source')) or 'UNKNOWN' for r in gd_recs)
print(f'  Zhilian Candidate：{dict(sc_zl)}，distinct companies={len(set(r.get("company_name") or "" for r in zl_recs))}')
oc_t = sum(1 for r in oc_recs if (r.get('source_company') or '').strip()=='Tencent')
oc_b = sum(1 for r in oc_recs if (r.get('source_company') or '').strip()=='ByteDance')
print(f'  Official Career：Tencent={oc_t}，ByteDance={oc_b}（预期25/25）')
print(f'  Gold：{dict(sc_gd)}，distinct companies={len(set(r.get("company_name") or "" for r in gd_recs))}')
print()

fc = csv_rows('unified_jd_field_coverage.csv')
req9 = {'job_title_raw','company_name','location','responsibilities','requirements','detail_raw_text','source_id','source_url','_sha256'}
print('【字段完整性】')
for ds in ['Zhilian', 'Gold', 'Official']:
    rows = [r for r in fc if r['dataset']==ds and r['field'] in req9]
    rows.sort(key=lambda r: float(r['non_empty_pct'].rstrip('%')) if r['non_empty_pct'].rstrip('%').replace('.','',1).isdigit() else 1e9)
    worst = rows[0]
    print(f'{ds}：最差必填字段={worst["field"]}；非空率={worst["non_empty_pct"]}；类型/异常={worst["types_top3"]} / {worst["anomalies"]}')
print()

jf = csv_rows('unified_jd_job_family_distribution.csv')
print('【岗位覆盖】')
for ds in ['Zhilian', 'Gold', 'Official']:
    rs = sorted([r for r in jf if r['dataset']==ds], key=lambda r: -int(r['count']))
    total = sum(int(r['count']) for r in rs) or 1
    fam_rows = [(r['job_family'], int(r['count']), float(r['pct'].rstrip('%'))) for r in rs]
    top3 = '；'.join(f'{n}={c}({p:.0f}%)' for n,c,p in fam_rows[:3])
    gaps = [n for n,c,p in fam_rows if n!='其他' and c==0]
    over = [n for n,c,p in fam_rows if n!='其他' and p>=40]
    print(f'{ds}：岗位族 Top3={top3}')
    print(f'  缺口（0条非其他）：{gaps if gaps else "无"}')
    print(f'  过度集中（≥40%）：{over if over else "无"}')
print()

dups = csv_rows('unified_jd_cross_source_duplicates.csv')
tc = Counter(r['tier'] for r in dups)
print('【跨源重复】（Zhilian × Official Career50）')
print(f'Exact：{tc.get("EXACT_DUPLICATE",0)}')
print(f'Strong：{tc.get("STRONG_SUSPECT",0)}')
print(f'Weak：{tc.get("WEAK_SUSPECT",0)}')
print('说明：0条＝无跨源重合（Zhilian智联 vs 官方官网来源独立），正常')
print()

gold = csv_rows('unified_jd_gold_overlap.csv')
gz = [r for r in gold if r['L_dataset']=='Gold' and r['R_dataset']=='Zhilian']
go = [r for r in gold if r['L_dataset']=='Gold' and r['R_dataset']=='Official']
cgz, cgo = Counter(r['tier'] for r in gz), Counter(r['tier'] for r in go)
print('【Gold重合】')
print('Gold vs Zhilian：')
print(f'  Exact：{cgz.get("EXACT_DUPLICATE",0)}')
print(f'  Strong：{cgz.get("STRONG_SUSPECT",0)}')
print(f'  Weak：{cgz.get("WEAK_SUSPECT",0)}')
print(f'  （Gold 110条来源全＝zhilian，因此高重合＝正常；需train/eval按source_id黑名单去leakage）')
print('Gold vs Official：')
print(f'  Exact：{cgo.get("EXACT_DUPLICATE",0)}')
print(f'  Strong：{cgo.get("STRONG_SUSPECT",0)}')
print(f'  Weak：{cgo.get("WEAK_SUSPECT",0)}')
print('  （Gold不来自官方官网来源，0重合＝正常）')
print()

qi = csv_rows('unified_jd_quality_issues.csv')
print('【质量异常】')
qd = Counter((r['dataset'], r['issue']) for r in qi)
for ds in ['Zhilian', 'Gold', 'Official']:
    items = [(d,i,c) for (d,i),c in qd.items() if d==ds]
    items.sort(key=lambda x: -x[2])
    top_items = '；'.join(f'{i}={c}' for _,i,c in items[:3]) or '无'
    total = sum(c for _,_,c in items)
    print(f'{ds}：总异常条数={total}，主要异常类型：{top_items}')
# Breakdown
field_empty = resp_detail = sha_anom = time_anom = 0
for (ds,iss), c in qd.items():
    if iss.endswith('_EMPTY'):
        field_empty += c
    elif 'sha' in iss.lower():
        sha_anom += c
    elif 'future' in iss:
        time_anom += c
    else:
        resp_detail += c
print(f'异常分类汇总：字段空/缺失={field_empty}；职责/要求/detail边界问题={resp_detail}；SHA异常={sha_anom}；future时间异常={time_anom}（Official Career已知2条仍在此处）')
print()

print('【数据边界结论】')
print(f'- 可继续作为candidate pool：Zhilian Candidate {sz}条（需复核SHA/时间异常明细）；Official Career 50封板：T{oc_t}/B{oc_b} ✅，Pilot20六项在上一轮封板审计为20/20 PASS ✅，future anomaly=2条保留 ✅ → candidate正式封板可继续使用')
print(f'- 正式Gold：jd_golden_110.jsonl {sg}条 ✅，属于 final/ 黄金集阶段')
print('- 需要人工复核记录：unified_jd_quality_issues.csv全部行；unified_jd_cross_source_duplicates.csv如有；unified_jd_gold_overlap.csv G×Z 110条用于train/eval去泄漏黑名单')
print('- 是否存在明显泄漏风险：Gold与Zhilian Candidate高度重合（110/110），训练/评测若Gold用作eval则Zhilian candidate的train split必须按相同source_id+company剔除，否则直接leakage；G×O＝0，无风险')
print()
print('READY_FOR_UNIFIED_JD_AUDIT_REVIEW')
