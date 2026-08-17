"""Fix and rebuild batch_backend_fullstack.jsonl.

Recalculates all derived fields: text_education, text_experience,
education_conflict, experience_conflict, and _sha256.

Run: python fix_sha256_backend.py
"""
import json
import hashlib
import re
import os

FILEPATH = os.path.join(os.path.dirname(__file__), "batch_backend_fullstack.jsonl")


def extract_text_education(text):
    for p in [
        r'(本科及以上学历|硕士及以上学历|本科及以上|硕士及以上|博士及以上)',
        r'(统招本科及以上|统招本科)',
        r'(本科|硕士|博士|大专|高中)(?:及以上)?(?:学历|学位)',
        r'学历[要求：:]\s*(本科|硕士|博士|大专|高中)',
    ]:
        m = re.search(p, text)
        if m:
            return m.group(1)
    return ""


def extract_text_experience(text):
    for p in [
        r'(\d+\s*[年]以上).*?(?:工作)?经验',
        r'(\d+\s*[-~～]\s*\d+\s*年).*?(?:工作)?经验',
        r'(\d+\s*[年]及以上).*?(?:工作)?经验',
        r'经验[要求：:]\s*(\d+[年]以上|\d+[-~]\d+年)',
        r'(经验不限|应届生)',
    ]:
        m = re.search(p, text)
        if m:
            return m.group(1)
    return ""


def check_education_conflict(ssr_edu, text_edu):
    if not ssr_edu or not text_edu:
        return False
    levels = {"博士": 5, "硕士": 4, "本科": 3, "大专": 2, "高中": 1, "学历不限": 0}
    s_key = ssr_edu.replace("及以上", "").replace("学历", "").strip()
    t_key = text_edu.replace("及以上", "").replace("学历", "").strip()
    s = levels.get(s_key, 0)
    t = levels.get(t_key, 0)
    return s != 0 and t != 0 and s != t


def check_experience_conflict(ssr_exp, text_exp):
    if not ssr_exp or not text_exp:
        return False
    # Normalize for comparison
    s = ssr_exp.lower().replace("经验不限", "0").replace("应届生", "0").replace(" ", "")
    t = text_exp.lower().replace("经验不限", "0").replace("应届生", "0").replace(" ", "")
    # If one is a range and the other is a single value, flag as conflict
    if ("-" in s or "~" in s or "～" in s) and ("-" not in t and "~" not in t and "～" not in t):
        return True
    if ("-" not in s and "~" not in s and "～" not in s) and ("-" in t or "~" in t or "～" in t):
        return True
    return s != t


# Read JSONL
lines = []
with open(FILEPATH, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            lines.append(line)

records = [json.loads(line) for line in lines]

# Recompute all derived fields
for r in records:
    # detail_raw_text must be resp + "\n" + req
    r["detail_raw_text"] = r["responsibilities"] + "\n" + r["requirements"]
    r["_sha256"] = hashlib.sha256(r["detail_raw_text"].encode("utf-8")).hexdigest()
    
    combined = r["responsibilities"] + r["requirements"]
    r["text_education"] = extract_text_education(combined)
    r["text_experience"] = extract_text_experience(combined)
    r["education_conflict"] = check_education_conflict(r["source_education"], r["text_education"])
    r["experience_conflict"] = check_experience_conflict(r["source_experience"], r["text_experience"])

# Write back
with open(FILEPATH, "w", encoding="utf-8") as f:
    for record in records:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

print(f"Updated {len(records)} records with real SHA-256 hashes and recomputed fields")

# Category stats
categories = {}
for r in records:
    title = r["job_title_raw"].lower()
    if "python" in title:
        cat = "Python开发"
    elif "go" in title or "golang" in title:
        cat = "Go开发"
    elif "php" in title:
        cat = "PHP开发"
    elif "全栈" in title:
        cat = "全栈开发"
    elif "java" in title:
        cat = "Java开发"
    elif "后端" in title:
        cat = "后端开发"
    else:
        cat = "其他"
    categories[cat] = categories.get(cat, 0) + 1

print("\nCategory stats:")
for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
    print(f"  {cat}: {count}")
print(f"\nTotal: {len(records)}")

# Conflict summary
edu_conflicts = sum(1 for r in records if r["education_conflict"])
exp_conflicts = sum(1 for r in records if r["experience_conflict"])
print(f"\nEducation conflicts: {edu_conflicts}")
print(f"Experience conflicts: {exp_conflicts}")

# Location distribution
locations = {}
for r in records:
    loc = r["location"]
    locations[loc] = locations.get(loc, 0) + 1
print("\nLocation distribution:")
for loc, count in sorted(locations.items(), key=lambda x: -x[1]):
    print(f"  {loc}: {count}")