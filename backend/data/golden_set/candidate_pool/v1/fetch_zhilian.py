"""Fetch Zhilian search results via API and extract source_ids."""
import json
import os
import re
import hashlib
import urllib.request
import urllib.parse
import ssl
import time
from datetime import datetime, timezone, timedelta

ssl._create_default_https_context = ssl._create_unverified_context

OUTPUT_DIR = r"D:\du_yan\jiebang_guashuai_jingsai\zhigang-compass\backend\data\golden_set\candidate_pool\v1"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Excluded source_ids
EXCLUDED = {
    "CCL1480117890J40845576605", "CC138117190J40879068905", "CCL1516918430J40789275606",
    "CC192921310J40831468409", "CC303218880J40962177002", "CC258760917J90250298000",
    "CC000544460J40670242116", "CC385622410J40787862106", "CCL1480117890J40603130605",
    "CC135794170J41002479902", "CC000283190J40710167711", "CC246691810J40831043903"
}

# Beijing timezone
CST = timezone(timedelta(hours=8))

# Search configs: (keyword, city_ids)
SEARCHES = [
    ("数据分析师", [530, 538, 539]),
    ("数据分析", [530, 765]),
    ("BI工程师", [530, 538]),
    ("大数据开发", [530, 538, 539]),
    ("数据工程师", [530, 765]),
    ("ETL工程师", [530]),
    ("算法工程师", [530, 538, 539]),
    ("推荐算法", [530, 765]),
    ("大模型", [530, 538, 539]),
    ("AI工程师", [530, 765]),
    ("机器学习", [530, 538]),
    ("AIGC", [530]),
]

def fetch_search(keyword, city_id, start=0):
    """Fetch search results from Zhilian API."""
    params = {
        "pageSize": 60,
        "cityId": city_id,
        "kw": keyword,
        "start": start,
        "workExperience": -1,
        "education": -1,
        "companyType": -1,
        "employmentType": -1,
        "jobWelfareTag": -1,
        "kt": 3,
        "_v": "0.1",
    }
    url = "https://fe-api.zhaopin.com/c/i/sou?" + urllib.parse.urlencode(params)
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://sou.zhaopin.com/",
    }
    
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read().decode("utf-8"))
        return data
    except Exception as e:
        print(f"  Error fetching {keyword} city={city_id}: {e}")
        return None

def parse_search_results(data):
    """Extract basic info from search API results."""
    results = []
    if not data or data.get("code") != 200:
        return results
    
    items = data.get("data", {}).get("results", [])
    for item in items:
        number = item.get("number", "")
        if not number or not number.startswith("CC"):
            continue
        if number in EXCLUDED:
            continue
        results.append({
            "source_id": number,
            "job_title_raw": item.get("title", ""),
            "company_name": item.get("company", {}).get("name", ""),
            "location": item.get("city", {}).get("display", ""),
            "salary": item.get("salary60", ""),
            "source_education": item.get("education", {}).get("name", ""),
            "source_experience": item.get("workingExp", {}).get("name", ""),
            "publish_time": item.get("publishTime", ""),
        })
    return results

def fetch_detail_page(source_id):
    """Fetch job detail page and extract description."""
    url = f"https://www.zhaopin.com/jobdetail/{source_id}.htm"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
    }
    
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        html = resp.read().decode("utf-8", errors="replace")
        return html
    except Exception as e:
        print(f"  Error fetching detail {source_id}: {e}")
        return None

def extract_from_initial_state(html):
    """Extract __INITIAL_STATE__ JSON from HTML."""
    match = re.search(r'__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>', html, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None

def parse_detail_html(html):
    """Parse detail page HTML for job description."""
    state = extract_from_initial_state(html)
    if not state:
        return None
    
    # Try to get description from jobDetail
    job_detail = state.get("jobDetail", {})
    if not job_detail:
        # Try alternate paths
        for key in state:
            if isinstance(state[key], dict) and "detailedPosition" in str(state[key]):
                job_detail = state[key]
                break
    
    detailed_position = job_detail.get("detailedPosition", {})
    description = detailed_position.get("description", "")
    
    if not description:
        # Try other paths
        description = job_detail.get("description", "")
    
    if not description:
        # Try to find description in any nested object
        def find_desc(obj, depth=0):
            if depth > 10:
                return ""
            if isinstance(obj, dict):
                if "description" in obj and isinstance(obj["description"], str) and len(obj["description"]) > 100:
                    return obj["description"]
                if "detailedPosition" in obj:
                    d = obj["detailedPosition"]
                    if isinstance(d, dict) and "description" in d:
                        return d["description"]
                for v in obj.values():
                    result = find_desc(v, depth + 1)
                    if result:
                        return result
            return ""
        description = find_desc(state)
    
    return {
        "description": description,
        "state": state,
    }

def split_jd_text(text):
    """Split JD text into responsibilities and requirements."""
    if not text:
        return "", ""
    
    # Common patterns for splitting
    patterns = [
        (r'任职要求[：:]', r'岗位要求[：:]'),
        (r'岗位职责[：:]', r'任职要求[：:]'),
        (r'工作职责[：:]', r'任职要求[：:]'),
        (r'岗位职责[：:]', r'岗位要求[：:]'),
        (r'工作内容[：:]', r'任职要求[：:]'),
        (r'职位描述[：:]', r'任职要求[：:]'),
        (r'职责描述[：:]', r'任职要求[：:]'),
        (r'【岗位职责】', r'【任职要求】'),
        (r'【工作职责】', r'【任职要求】'),
        (r'主要职责[：:]', r'任职资格[：:]'),
        (r'岗位职责[：:]', r'职位要求[：:]'),
    ]
    
    for resp_pattern, req_pattern in patterns:
        resp_match = re.search(resp_pattern, text)
        req_match = re.search(req_pattern, text)
        if resp_match and req_match and resp_match.start() < req_match.start():
            resp_start = resp_match.end()
            req_start = req_match.start()
            responsibilities = text[resp_start:req_start].strip()
            requirements = text[req_match.end():].strip()
            return responsibilities, requirements
    
    # Try single split pattern
    for pattern in [r'任职要求[：:]', r'岗位要求[：:]', r'职位要求[：:]']:
        match = re.search(pattern, text)
        if match:
            idx = match.start()
            # Try to split at a reasonable point
            if idx > 50:
                responsibilities = text[:idx].strip()
                requirements = text[match.end():].strip()
                return responsibilities, requirements
    
    # If no clear split, return entire text as requirements
    return "", text.strip()

def extract_education_from_text(text):
    """Extract education requirement from text."""
    patterns = [
        r'(本科及以上学历|硕士及以上学历|博士学历|本科以上学历|硕士以上学历)',
        r'(本科|硕士|博士|大专|学历不限)[\s]*[及以]*学历',
        r'学历[要求：:]*\s*(本科|硕士|博士|大专|不限)',
        r'(全日制\s*(本科|硕士|博士))',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return ""

def extract_experience_from_text(text):
    """Extract experience requirement from text."""
    patterns = [
        r'(\d+[年\s]*以上[\s]*(相关)?(工作)?经验)',
        r'(\d+-\d+年[\s]*(相关)?(工作)?经验)',
        r'(经验不限|应届生|无经验)',
        r'(有\s*\d+\s*年[\s]*(以上)?[\s]*(相关)?(工作)?经验)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return ""

def extract_tags_from_skill_list(skill_list):
    """Extract tags from skill list."""
    if not skill_list:
        return []
    tags = []
    for skill in skill_list:
        if isinstance(skill, dict):
            name = skill.get("name", "")
            if name:
                tags.append(name)
        elif isinstance(skill, str):
            tags.append(skill)
    return tags

def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

# ========== MAIN ==========

all_candidates = {}
seen_ids = set()

print("=== Phase 1: Fetching search results ===")
for keyword, city_ids in SEARCHES:
    for city_id in city_ids:
        print(f"Searching: {keyword} in city {city_id}...")
        for start in [0, 60]:
            data = fetch_search(keyword, city_id, start)
            if data:
                results = parse_search_results(data)
                for r in results:
                    sid = r["source_id"]
                    if sid not in seen_ids:
                        seen_ids.add(sid)
                        all_candidates[sid] = r
                print(f"  Got {len(results)} results (start={start})")
            time.sleep(0.5)

print(f"\nTotal unique candidates from search: {len(all_candidates)}")

# Phase 2: Fetch detail pages
print("\n=== Phase 2: Fetching detail pages ===")
success_count = 0
results = []

for sid, info in list(all_candidates.items())[:80]:  # Limit to first 80 to save time
    if success_count >= 30:
        break
    
    print(f"Fetching detail: {sid} ({info['job_title_raw']})...")
    html = fetch_detail_page(sid)
    if not html:
        continue
    
    parsed = parse_detail_html(html)
    if not parsed or not parsed["description"]:
        print(f"  No description found, skipping")
        continue
    
    desc = parsed["description"]
    state = parsed["state"]
    
    # Clean HTML tags from description
    desc_clean = re.sub(r'<[^>]+>', '', desc)
    desc_clean = re.sub(r'&nbsp;', ' ', desc_clean)
    desc_clean = re.sub(r'&lt;', '<', desc_clean)
    desc_clean = re.sub(r'&gt;', '>', desc_clean)
    desc_clean = re.sub(r'&amp;', '&', desc_clean)
    desc_clean = re.sub(r'\s+', ' ', desc_clean).strip()
    
    if len(desc_clean) < 50:
        print(f"  Description too short ({len(desc_clean)} chars), skipping")
        continue
    
    # Split into responsibilities and requirements
    responsibilities, requirements = split_jd_text(desc_clean)
    detail_raw = responsibilities + "\n" + requirements if responsibilities else requirements
    
    if len(detail_raw) < 50:
        print(f"  detail_raw too short, skipping")
        continue
    
    # Extract metadata from state
    job_detail = state.get("jobDetail", {})
    skill_list = job_detail.get("skillList", []) or state.get("skillList", [])
    
    # Extract education and experience from text
    text_edu = extract_education_from_text(desc_clean)
    text_exp = extract_experience_from_text(desc_clean)
    
    # Check conflicts
    source_edu = info.get("source_education", "")
    source_exp = info.get("source_experience", "")
    
    edu_conflict = False
    if text_edu and source_edu:
        # Simple check: if text mentions bachelor but source says master, etc.
        if ("本科" in text_edu and "硕士" in source_edu) or ("硕士" in text_edu and "本科" in source_edu):
            if "及以上" not in text_edu and "以上" not in text_edu:
                edu_conflict = True
    
    exp_conflict = False
    if text_exp and source_exp:
        # Extract years
        text_years = re.search(r'(\d+)', text_exp)
        src_years = re.search(r'(\d+)', source_exp)
        if text_years and src_years:
            if abs(int(text_years.group(1)) - int(src_years.group(1))) > 2:
                exp_conflict = True
    
    crawl_time = datetime.now(CST).isoformat()
    
    record = {
        "source": "zhilian",
        "source_id": sid,
        "source_url": f"https://www.zhaopin.com/jobdetail/{sid}.htm",
        "job_title_raw": info["job_title_raw"],
        "company_name": info["company_name"],
        "location": info["location"],
        "salary": info["salary"],
        "source_education": source_edu,
        "source_experience": source_exp,
        "text_education": text_edu,
        "text_experience": text_exp,
        "education_conflict": edu_conflict,
        "experience_conflict": exp_conflict,
        "publish_time": info.get("publish_time", ""),
        "crawl_time": crawl_time,
        "responsibilities": responsibilities,
        "requirements": requirements,
        "detail_raw_text": detail_raw,
        "tags": extract_tags_from_skill_list(skill_list),
        "_sha256": sha256(detail_raw),
    }
    
    results.append(record)
    success_count += 1
    print(f"  SUCCESS! ({success_count}/30) [{len(desc_clean)} chars]")
    time.sleep(0.3)

# Save results
output_path = os.path.join(OUTPUT_DIR, "batch_data_ai.json")
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print(f"\n=== COMPLETE ===")
print(f"Total collected: {success_count}")
print(f"Saved to: {output_path}")

# Print summary
categories = {}
for r in results:
    title = r["job_title_raw"]
    if "数据分析" in title:
        cat = "数据分析"
    elif "算法" in title or "推荐" in title:
        cat = "算法"
    elif "大模型" in title or "AI" in title or "AIGC" in title or "机器学习" in title or "深度学习" in title:
        cat = "AI/大模型"
    elif "数据工程" in title or "ETL" in title or "大数据" in title or "数据开发" in title or "BI" in title:
        cat = "数据工程"
    else:
        cat = "其他"
    categories[cat] = categories.get(cat, 0) + 1

print("\nCategory breakdown:")
for cat, count in sorted(categories.items()):
    print(f"  {cat}: {count}")

print("\nSource IDs:")
for r in results:
    print(f"  {r['source_id']} - {r['job_title_raw']} - {r['company_name']}")