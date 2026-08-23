"""Expanded JD candidate pool collection: 120-150 real JDs from zhilian.com.

Covers 12+ job categories. Quality checks, dedup, tiering, output generation.

Constraints:
- No login bypass, no CAPTCHA bypass, no anti-crawling bypass
- detail_raw_text = responsibilities + "\\n" + requirements (NO field concatenation)
- SHA256 based on responsibilities + "\\n" + requirements
- Dynamic crawl_time
- SimHash based on responsibilities + requirements (no field prefixes)
"""

import asyncio
import csv
import hashlib
import json
import re
import sys
import time
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root
_BACKEND_DIR = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_BACKEND_DIR))
sys.path.insert(0, str(_BACKEND_DIR / "data"))

import httpx
from crawlers.zhilian_detail import extract_job_detail

# ── Configuration ──
CST = timezone(timedelta(hours=8))
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)
OUTPUT_DIR = Path(__file__).resolve().parent
RAW_PATH = OUTPUT_DIR / "real_jd_candidates_raw.jsonl"
CLEAN_PATH = OUTPUT_DIR / "real_jd_candidates_clean.jsonl"
CLEAN_CSV_PATH = OUTPUT_DIR / "real_jd_candidates_clean.csv"
REVIEW_PATH = OUTPUT_DIR / "real_jd_review_required.csv"
REJECTED_PATH = OUTPUT_DIR / "real_jd_rejected.csv"
REPORT_PATH = OUTPUT_DIR / "real_jd_collection_quality_report.md"

TARGET_RAW = 150
TARGET_ACCEPTED = 100
DETAIL_TIMEOUT = 20
SEARCH_TIMEOUT = 30
DELAY_MIN = 8
DELAY_MAX = 15

# ── Job Categories & Search Keywords ──
# Each category maps to search keywords for zhilian
CATEGORY_KEYWORDS = {
    "后端开发": ["Python开发", "Java开发", "Go开发", "C++开发", "PHP开发", "Node.js开发", "后端开发"],
    "前端开发": ["前端开发", "React", "Vue", "Web前端"],
    "全栈开发": ["全栈开发", "全栈工程师"],
    "测试": ["测试工程师", "软件测试", "自动化测试", "QA"],
    "运维/DevOps": ["运维工程师", "DevOps", "SRE", "系统运维"],
    "数据分析": ["数据分析师", "数据分析", "BI工程师"],
    "数据工程/大数据": ["数据工程师", "大数据开发", "ETL工程师", "数据仓库"],
    "算法": ["算法工程师", "推荐算法", "搜索算法", "NLP算法"],
    "AI/大模型": ["大模型", "AI工程师", "机器学习", "深度学习", "AIGC"],
    "嵌入式/C++": ["嵌入式开发", "C++开发", "单片机", "驱动开发"],
    "网络/安全": ["网络安全", "信息安全", "安全工程师", "渗透测试"],
    "其他技术岗": ["架构师", "技术经理", "技术总监", "软件开发"],
}

# Cities for search
CITIES = ["北京", "上海", "深圳", "杭州", "广州", "成都", "南京", "武汉"]

ZHILIAN_CITY_CODES = {
    "北京": "530", "上海": "538", "深圳": "765",
    "杭州": "539", "广州": "763", "成都": "801",
    "南京": "635", "武汉": "736",
}

# ── Pilot source_ids to exclude ──
PILOT_IDS = {
    "CCL1480117890J40845576605", "CC138117190J40879068905",
    "CCL1516918430J40789275606", "CC192921310J40831468409",
    "CC303218880J40962177002", "CC258760917J90250298000",
    "CC000544460J40670242116", "CC385622410J40787862106",
    "CCL1480117890J40603130605", "CC135794170J41002479902",
    "CC000283190J40710167711", "CC246691810J40831043903",
}

# ── Helpers ──
def now_iso() -> str:
    return datetime.now(CST).isoformat()

def compute_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def compute_simhash(text: str) -> int:
    """64-bit SimHash based on tokenized text."""
    if not text:
        return 0
    tokens = re.findall(r'[\w\u4e00-\u9fff]+', text.lower())
    if not tokens:
        return 0
    bits = 64
    weights = [0] * bits
    for token in tokens:
        h = hash(token) & 0xFFFFFFFFFFFFFFFF
        for i in range(bits):
            if (h >> i) & 1:
                weights[i] += 1
            else:
                weights[i] -= 1
    fingerprint = 0
    for i in range(bits):
        if weights[i] > 0:
            fingerprint |= (1 << i)
    return fingerprint

def hamming_distance(a: int, b: int) -> int:
    x = a ^ b
    dist = 0
    while x:
        dist += 1
        x &= x - 1
    return dist

def extract_education_from_text(text: str) -> str:
    """Extract education requirement from text."""
    patterns = [
        r'(博士(?:研究生)?及以上学历)',
        r'(硕士(?:研究生)?及以上学历)',
        r'(研究生及以上学历)',
        r'(本科及以上学历)',
        r'(大专及以上学历)',
        r'(博士(?:研究生)?)',
        r'(硕士(?:研究生)?)',
        r'(本科)',
        r'(大专)',
        r'(学历不限)',
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(1)
    return ""

def extract_experience_from_text(text: str) -> str:
    """Extract experience requirement from text."""
    patterns = [
        r'(\d+[-\s]*\d+\s*年以上?(?:相关)?(?:工作)?经验)',
        r'(\d+\s*年以上?(?:相关)?(?:工作)?经验)',
        r'(经验不限)',
        r'(应届生)',
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(1)
    return ""

def check_education_conflict(source_edu: str, text_edu: str) -> bool:
    """Check if source and text education conflict."""
    if not source_edu or not text_edu:
        return False
    source_lower = source_edu.lower().replace(" ", "")
    text_lower = text_edu.lower().replace(" ", "")
    # Simple heuristic: if source says "大专" but text says "本科及以上"
    edu_levels = {"博士": 5, "硕士": 4, "研究生": 4, "本科": 3, "大专": 2, "学历不限": 1}
    source_level = None
    text_level = None
    for k, v in edu_levels.items():
        if k in source_lower:
            source_level = v
            break
    for k, v in edu_levels.items():
        if k in text_lower:
            text_level = v
            break
    if source_level is not None and text_level is not None:
        if abs(source_level - text_level) >= 2:
            return True
    return False

def check_experience_conflict(source_exp: str, text_exp: str) -> bool:
    """Check if source and text experience conflict."""
    if not source_exp or not text_exp:
        return False
    if source_exp == "经验不限" and text_exp and "经验不限" not in text_exp:
        return True
    return False

def map_category(title: str, tags: list) -> str:
    """Map a JD to a job category based on title and tags."""
    title_lower = title.lower() if title else ""
    tags_lower = " ".join(tags).lower() if tags else ""

    category_rules = [
        ("AI/大模型", ["大模型", "ai", "aigc", "机器学习", "深度学习", "自然语言处理", "nlp", "llm", "gpt"]),
        ("数据工程/大数据", ["数据工程", "大数据", "etl", "数据仓库", "hadoop", "spark", "flink", "数据开发"]),
        ("数据分析", ["数据分析", "bi", "商业分析", "数据运营", "数据产品"]),
        ("嵌入式/C++", ["嵌入式", "单片机", "驱动开发", "arm", "stm32", "mcu", "rtos", "linux内核"]),
        ("网络/安全", ["网络安全", "信息安全", "渗透", "安全工程", "安全运维", "安全测试", "攻防"]),
        ("运维/DevOps", ["运维", "devops", "sre", "系统运维", "运维开发", "k8s", "kubernetes", "docker"]),
        ("测试", ["测试", "qa", "质量", "自动化测试", "测试开发"]),
        ("前端开发", ["前端", "web前端", "h5", "react", "vue", "angular", "小程序", "flutter", "ios", "android"]),
        ("全栈开发", ["全栈", "fullstack", "full stack"]),
        ("算法", ["算法", "nlp", "cv", "图像", "推荐", "搜索", "广告", "语音"]),
        ("后端开发", ["后端", "java", "python", "go", "golang", "php", "node", "c++", "c#", "rust", "服务端"]),
    ]
    for cat, keywords in category_rules:
        for kw in keywords:
            if kw in title_lower or kw in tags_lower:
                return cat
    return "其他技术岗"

# ── Collection Functions ──
async def search_zhilian(client: httpx.AsyncClient, keyword: str, city: str, page: int = 1) -> list[dict]:
    """Search zhilian for job listings via SSR."""
    city_code = ZHILIAN_CITY_CODES.get(city)
    if not city_code:
        return []
    url = f"https://sou.zhaopin.com/?jl={city_code}&kw={keyword}&pn={page}"
    try:
        resp = await client.get(
            url,
            headers={"User-Agent": UA},
            timeout=SEARCH_TIMEOUT,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return []
        text = resp.text
        match = re.search(r"__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>", text, re.DOTALL)
        if not match:
            return []
        data = json.loads(match.group(1))
        results = []
        _extract_jobs_from_ssr(data, results, keyword, city)
        return results
    except Exception:
        return []

def _extract_jobs_from_ssr(obj, results: list, keyword: str, city: str):
    """Recursively extract job listings from SSR JSON."""
    if isinstance(obj, dict):
        number = obj.get("number")
        title = obj.get("title") or obj.get("jobName") or obj.get("name")
        if number and title and isinstance(number, str) and number.startswith("CC"):
            if not any(r["source_id"] == number for r in results):
                results.append({
                    "source_id": number,
                    "title": str(title),
                    "company": str(obj.get("company") or obj.get("companyName") or ""),
                    "location": str(obj.get("city") or obj.get("workCity") or city),
                    "salary": str(obj.get("salary") or obj.get("salaryDesc") or ""),
                    "experience": str(obj.get("experience") or obj.get("workingExp") or ""),
                    "education": str(obj.get("education") or obj.get("eduLevel") or ""),
                    "post_date": str(obj.get("publishTime") or obj.get("publishDate") or ""),
                    "tags": [],
                    "keyword": keyword,
                    "city": city,
                })
        for v in obj.values():
            _extract_jobs_from_ssr(v, results, keyword, city)
    elif isinstance(obj, list):
        for item in obj:
            _extract_jobs_from_ssr(item, results, keyword, city)

async def fetch_detail(client: httpx.AsyncClient, source_id: str) -> dict | None:
    """Fetch and parse a zhilian detail page."""
    url = f"https://www.zhaopin.com/jobdetail/{source_id}.htm"
    try:
        resp = await client.get(
            url,
            headers={"User-Agent": UA},
            timeout=DETAIL_TIMEOUT,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return None
        detail = extract_job_detail(resp.text)
        if not detail.get("description") and not detail.get("requirements"):
            return None
        return {
            "source_id": source_id,
            "source_url": url,
            "html": resp.text,
            "description": detail["description"],
            "requirements": detail["requirements"],
        }
    except Exception:
        return None

def build_jd_record(
    source: str,
    source_id: str,
    source_url: str,
    title: str,
    company: str,
    location: str,
    salary: str,
    source_education: str,
    source_experience: str,
    description: str,
    requirements: str,
    post_date: str,
    tags: list | None = None,
) -> dict:
    """Build a standardized JD record with all required fields."""
    detail_raw_text = ""
    if description:
        detail_raw_text = description
    if requirements:
        if detail_raw_text:
            detail_raw_text += "\n" + requirements
        else:
            detail_raw_text = requirements

    # Extract education/experience from text
    combined_text = "\n".join([description, requirements])
    text_education = extract_education_from_text(combined_text)
    text_experience = extract_experience_from_text(combined_text)

    # Check conflicts
    edu_conflict = check_education_conflict(source_education, text_education)
    exp_conflict = check_experience_conflict(source_experience, text_experience)

    # SHA256 based on responsibilities + requirements
    sha_text = (description or "") + "\n" + (requirements or "")
    sha_text = sha_text.strip()
    sha256 = compute_sha256(sha_text) if sha_text else ""

    return {
        "source": source,
        "source_id": source_id,
        "source_url": source_url,
        "job_title_raw": title,
        "company_name": company,
        "location": location,
        "salary": salary,
        "source_education": source_education,
        "source_experience": source_experience,
        "text_education": text_education,
        "text_experience": text_experience,
        "education_conflict": edu_conflict,
        "experience_conflict": exp_conflict,
        "publish_time": post_date,
        "crawl_time": now_iso(),
        "detail_raw_text": detail_raw_text,
        "responsibilities": description or "",
        "requirements": requirements or "",
        "tags": tags or [],
        "_sha256": sha256,
    }

# ── Main Collection ──
async def collect():
    print("=" * 70)
    print("Expanded JD Candidate Pool Collection")
    print(f"Start: {now_iso()}")
    print(f"Target: {TARGET_RAW} raw JDs across 12+ categories")
    print("=" * 70)

    # Track seen IDs
    seen_ids = set(PILOT_IDS)
    collected = []
    stats = {
        "search_attempts": 0,
        "search_results": 0,
        "detail_fetched": 0,
        "detail_empty": 0,
        "detail_failed": 0,
        "category_counts": {},
    }

    # Build search tasks: spread across categories and cities
    # For each category, pick 1-2 keywords and 2-3 cities
    search_tasks = []
    for category, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords[:3]:  # max 3 keywords per category
            for city in random.sample(CITIES, min(3, len(CITIES))):
                search_tasks.append((category, kw, city))

    random.shuffle(search_tasks)

    async with httpx.AsyncClient(
        timeout=DETAIL_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": UA},
    ) as client:
        # ── Phase 1: Search for source_ids ──
        print(f"\n[Phase 1] Searching zhilian for {len(search_tasks)} keyword/city combos...")
        search_jobs = []
        for category, kw, city in search_tasks:
            if len(search_jobs) >= 300:
                break
            stats["search_attempts"] += 1
            results = await search_zhilian(client, kw, city, page=1)
            stats["search_results"] += len(results)
            for r in results:
                r["category"] = category
            search_jobs.extend(results)
            print(f"  [{category}] {kw} @ {city}: {len(results)} results")
            await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

        # Deduplicate by source_id
        unique_jobs = []
        seen_ids = set(PILOT_IDS)
        for j in search_jobs:
            if j["source_id"] not in seen_ids:
                seen_ids.add(j["source_id"])
                unique_jobs.append(j)

        print(f"\n  Unique new source_ids: {len(unique_jobs)}")

        # ── Phase 2: Fetch detail pages ──
        print(f"\n[Phase 2] Fetching detail pages for up to {TARGET_RAW} JDs...")
        for i, job in enumerate(unique_jobs):
            if len(collected) >= TARGET_RAW:
                break

            source_id = job["source_id"]
            print(f"  [{len(collected)+1}/{TARGET_RAW}] {source_id} ({job.get('title', 'N/A')[:40]})...", end=" ")

            detail = await fetch_detail(client, source_id)
            if detail:
                # Parse SSR for metadata
                title = job.get("title", "")
                company = job.get("company", "")
                location = job.get("location", "")
                salary = job.get("salary", "")
                source_education = job.get("education", "")
                source_experience = job.get("experience", "")
                post_date = job.get("post_date", "")

                # Try to extract more metadata from SSR
                ssr_match = re.search(
                    r"__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>",
                    detail["html"],
                    re.DOTALL,
                )
                tags = []
                if ssr_match:
                    try:
                        ssr_data = json.loads(ssr_match.group(1))
                        jd = (ssr_data.get("jobDetail") or {}).get("detailedPosition") or {}
                        if not title:
                            title = jd.get("title") or jd.get("jobName") or ""
                        if not company:
                            company = jd.get("companyName") or jd.get("company") or ""
                        if not location:
                            location = jd.get("workCity") or jd.get("city") or ""
                        if not salary:
                            salary = jd.get("salary") or jd.get("salaryDesc") or ""
                        if not source_education:
                            source_education = jd.get("eduLevel") or jd.get("education") or ""
                        if not source_experience:
                            source_experience = jd.get("workingExp") or jd.get("experience") or ""
                        if not post_date:
                            post_date = jd.get("publishTime") or jd.get("publishDate") or ""
                        skill_list = jd.get("skillList") or jd.get("skills") or []
                        if isinstance(skill_list, list):
                            tags = [s.get("skillName", s) if isinstance(s, dict) else str(s) for s in skill_list]
                    except (json.JSONDecodeError, KeyError):
                        pass

                # Also try title from HTML
                if not title:
                    title_match = re.search(r"<title>([^<]+)</title>", detail["html"])
                    if title_match:
                        title = title_match.group(1).strip()
                        title = re.sub(r"\s*[-–—|]\s*智联招聘.*$", "", title)
                        title = re.sub(r"\s*-\s*智联招聘.*$", "", title)

                record = build_jd_record(
                    source="zhilian",
                    source_id=source_id,
                    source_url=f"https://www.zhaopin.com/jobdetail/{source_id}.htm",
                    title=title,
                    company=company,
                    location=location,
                    salary=salary,
                    source_education=source_education,
                    source_experience=source_experience,
                    description=detail["description"],
                    requirements=detail["requirements"],
                    post_date=post_date,
                    tags=tags,
                )
                record["search_category"] = job.get("category", "其他技术岗")
                collected.append(record)
                stats["detail_fetched"] += 1
                print(f"OK ({len(detail['description'])} + {len(detail['requirements'])} chars)")
            else:
                stats["detail_empty"] += 1
                print("NO CONTENT")

            await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    # Save raw
    print(f"\n{'=' * 70}")
    print(f"Collection complete: {len(collected)} JDs collected")
    print(f"{'=' * 70}")

    with open(RAW_PATH, "w", encoding="utf-8") as f:
        for r in collected:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"Saved raw JDs to {RAW_PATH}")

    return collected, stats

# ── Quality Checks & Tiering ──
def run_quality_checks(records: list[dict]) -> dict:
    """Run all quality checks and classify records into accepted/review_required/rejected."""
    n = len(records)

    # Assign categories
    for r in records:
        r["job_category"] = map_category(r["job_title_raw"], r.get("tags", []))

    # ── Check 1: Empty detail_raw_text ──
    empty_detail = [r for r in records if not r["detail_raw_text"].strip()]

    # ── Check 2: Text length ──
    for r in records:
        r["low_information"] = len(r["detail_raw_text"]) < 200

    # ── Check 3: Empty title ──
    empty_title = [r for r in records if not r["job_title_raw"].strip()]

    # ── Check 4: Responsibilities/Requirements ──
    no_resp = [r for r in records if not r["responsibilities"].strip()]
    no_req = [r for r in records if not r["requirements"].strip()]

    # ── Check 5: Source URL ──
    no_url = [r for r in records if not r["source_url"].strip()]

    # ── Check 6: SHA256 exact duplicate ──
    sha_groups = {}
    for i, r in enumerate(records):
        h = r["_sha256"]
        sha_groups.setdefault(h, []).append(i)
    exact_dup_groups = {h: idxs for h, idxs in sha_groups.items() if len(idxs) > 1}

    # ── Check 7: URL duplicate ──
    url_groups = {}
    for i, r in enumerate(records):
        u = r["source_url"]
        url_groups.setdefault(u, []).append(i)
    url_dup_groups = {u: idxs for u, idxs in url_groups.items() if len(idxs) > 1}

    # ── Check 8: SimHash approximate duplicate ──
    # Based on responsibilities + requirements (NOT field-prefixed text)
    simhash_values = []
    for r in records:
        text = (r["responsibilities"] or "") + "\n" + (r["requirements"] or "")
        simhash_values.append(compute_simhash(text.strip()))

    approx_dup_pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            if simhash_values[i] == 0 or simhash_values[j] == 0:
                continue
            if hamming_distance(simhash_values[i], simhash_values[j]) <= 3:
                approx_dup_pairs.append((i, j))

    # ── Tiering ──
    accepted = []
    review_required = []
    rejected = []

    # Track which indices are in exact dup groups (keep first, reject rest)
    exact_dup_rejected = set()
    for h, idxs in exact_dup_groups.items():
        for idx in idxs[1:]:  # Keep first, reject rest
            exact_dup_rejected.add(idx)

    # Track approximate dup candidates
    approx_dup_candidates = set()
    for i, j in approx_dup_pairs:
        approx_dup_candidates.add(i)
        approx_dup_candidates.add(j)

    for i, r in enumerate(records):
        reasons = []

        # Rejected checks
        if i in exact_dup_rejected:
            r["rejection_reason"] = "exact_duplicate"
            rejected.append(r)
            continue

        if not r["detail_raw_text"].strip():
            r["rejection_reason"] = "empty_detail"
            rejected.append(r)
            continue

        if not r["source_url"].strip():
            r["rejection_reason"] = "no_source_url"
            rejected.append(r)
            continue

        if not r["job_title_raw"].strip():
            r["rejection_reason"] = "empty_title"
            rejected.append(r)
            continue

        if not r["responsibilities"].strip() and not r["requirements"].strip():
            r["rejection_reason"] = "no_duties_or_requirements"
            rejected.append(r)
            continue

        # Check for URL duplicate
        url_dup = False
        for u, idxs in url_dup_groups.items():
            if i in idxs and idxs.index(i) > 0:
                url_dup = True
                break
        if url_dup:
            r["rejection_reason"] = "url_duplicate"
            rejected.append(r)
            continue

        # Review required checks
        if r["education_conflict"]:
            reasons.append("education_conflict")
        if r["experience_conflict"]:
            reasons.append("experience_conflict")
        if r["low_information"]:
            reasons.append("low_information")
        if i in approx_dup_candidates:
            r["duplicate_review_required"] = True
            reasons.append("approximate_duplicate")

        if reasons:
            r["review_reasons"] = "; ".join(reasons)
            review_required.append(r)
        else:
            accepted.append(r)

    return {
        "total": n,
        "accepted": accepted,
        "review_required": review_required,
        "rejected": rejected,
        "empty_detail": empty_detail,
        "empty_title": empty_title,
        "no_resp": no_resp,
        "no_req": no_req,
        "no_url": no_url,
        "exact_dup_groups": exact_dup_groups,
        "url_dup_groups": url_dup_groups,
        "approx_dup_pairs": approx_dup_pairs,
        "simhash_values": simhash_values,
    }

# ── Output Generation ──
def generate_outputs(records: list, qc: dict, stats: dict):
    """Generate all output files: clean JSONL, CSV, review CSV, rejected CSV, report."""

    # ── Save clean JSONL (all accepted + review_required) ──
    clean_records = qc["accepted"] + qc["review_required"]
    with open(CLEAN_PATH, "w", encoding="utf-8") as f:
        for r in clean_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"Saved {len(clean_records)} clean records to {CLEAN_PATH}")

    # ── Save clean CSV ──
    csv_fields = [
        "source", "source_id", "source_url", "job_title_raw", "job_category",
        "company_name", "location", "salary",
        "source_education", "source_experience", "text_education", "text_experience",
        "education_conflict", "experience_conflict",
        "publish_time", "crawl_time",
        "responsibilities", "requirements", "detail_raw_text",
        "tags", "_sha256", "low_information",
    ]
    with open(CLEAN_CSV_PATH, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=csv_fields, extrasaction="ignore")
        writer.writeheader()
        for r in clean_records:
            row = dict(r)
            if isinstance(row.get("tags"), list):
                row["tags"] = "; ".join(row["tags"])
            writer.writerow(row)
    print(f"Saved clean CSV to {CLEAN_CSV_PATH}")

    # ── Save review_required CSV ──
    review_fields = csv_fields + ["review_reasons", "duplicate_review_required"]
    with open(REVIEW_PATH, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=review_fields, extrasaction="ignore")
        writer.writeheader()
        for r in qc["review_required"]:
            row = dict(r)
            if isinstance(row.get("tags"), list):
                row["tags"] = "; ".join(row["tags"])
            writer.writerow(row)
    print(f"Saved {len(qc['review_required'])} review_required to {REVIEW_PATH}")

    # ── Save rejected CSV ──
    rejected_fields = csv_fields + ["rejection_reason"]
    with open(REJECTED_PATH, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rejected_fields, extrasaction="ignore")
        writer.writeheader()
        for r in qc["rejected"]:
            row = dict(r)
            if isinstance(row.get("tags"), list):
                row["tags"] = "; ".join(row["tags"])
            writer.writerow(row)
    print(f"Saved {len(qc['rejected'])} rejected to {REJECTED_PATH}")

    # ── Generate Quality Report ──
    n = qc["total"]
    a = len(qc["accepted"])
    rr = len(qc["review_required"])
    rj = len(qc["rejected"])

    # Field completeness
    fields = [
        "source_url", "detail_raw_text", "responsibilities", "requirements",
        "source_education", "source_experience", "text_education", "text_experience",
        "publish_time", "salary",
    ]
    field_stats = {}
    for field in fields:
        filled = len([r for r in records if r.get(field) and str(r.get(field, "")).strip()])
        rate = 100 * filled // n if n else 0
        field_stats[field] = (filled, rate)

    # Source distribution
    sources = {}
    for r in records:
        s = r["source"]
        sources[s] = sources.get(s, 0) + 1

    # Category distribution
    categories = {}
    for r in records:
        cat = r.get("job_category", "未分类")
        categories[cat] = categories.get(cat, 0) + 1

    # Quality metrics
    low_info = [r for r in records if r.get("low_information")]
    edu_conflicts = [r for r in records if r.get("education_conflict")]
    exp_conflicts = [r for r in records if r.get("experience_conflict")]
    no_url = [r for r in records if not r.get("source_url", "").strip()]

    # Length stats
    lengths = sorted([len(r["detail_raw_text"]) for r in records if r["detail_raw_text"].strip()])

    report = [
        f"# JD Candidate Pool — 质量报告",
        f"",
        f"**生成时间**: {now_iso()}",
        f"**采集方式**: 智联招聘公开详情页（SSR，无需登录）",
        f"**目标**: 120-150 条原始候选池，最终 ≥ 100 条 accepted",
        f"",
        f"---",
        f"",
        f"## 一、采集总数",
        f"",
        f"| 层级 | 数量 | 说明 |",
        f"|------|------|------|",
        f"| 原始采集 | {n} | 从智联招聘详情页采集 |",
        f"| **accepted** | **{a}** | 直接进入人工标注 |",
        f"| review_required | {rr} | 存在冲突/低信息/近似重复，需人工复核 |",
        f"| rejected | {rj} | 明显质量问题，已记录原因 |",
        f"",
        f"**是否达到 ≥100 条 accepted**: {'✅ 是' if a >= 100 else '❌ 否（差 ' + str(100-a) + ' 条）'}",
        f"",
        f"---",
        f"",
        f"## 二、来源分布",
        f"",
        f"| 平台 | 数量 | 备注 |",
        f"|------|------|------|",
    ]
    for s, c in sources.items():
        report.append(f"| {s}（智联招聘） | {c} | 公开详情页，SSR数据，无需登录 |")
    report.append("")
    report.append("### 其他平台状态")
    report.append("")
    report.append("| 平台 | 状态 | 原因 |")
    report.append("|------|------|------|")
    report.append("| BOSS直聘 | ❌ 不可用 | 需要登录Cookie，违反\"不登录绕过\"原则 |")
    report.append("| 脉脉 | ❌ 不可用 | 仅采集脉脉自身招聘页（飞书），非全量职位；需CDP浏览器 |")
    report.append("| Indeed | ❌ 不可用 | 国际平台，需代理（未配置） |")
    report.append("| Monster | ❌ 不可用 | 国际平台，需代理（未配置） |")
    report.append("| Glassdoor | ❌ 不可用 | 国际平台，需代理（未配置） |")
    report.append("| LinkedIn | ❌ 不可用 | 国际平台，需代理（未配置） |")
    report.append("")

    report += [
        f"---",
        f"",
        f"## 三、岗位分布",
        f"",
        f"| 岗位类别 | 数量 | 占比 |",
        f"|----------|------|------|",
    ]
    for cat in sorted(categories.keys()):
        c = categories[cat]
        pct = f"{100*c//n}%" if n else "0%"
        report.append(f"| {cat} | {c} | {pct} |")

    report += [
        f"",
        f"---",
        f"",
        f"## 四、字段完整率",
        f"",
        f"| 字段 | 完整数 | 完整率 | 备注 |",
        f"|------|--------|--------|------|",
    ]
    field_notes = {
        "source_url": "均为智联详情页URL",
        "detail_raw_text": "responsibilities + \\\\n + requirements",
        "responsibilities": "从页面SSR提取的岗位职责",
        "requirements": "从页面SSR提取的任职要求",
        "source_education": "SSR metadata中的学历",
        "source_experience": "SSR metadata中的经验",
        "text_education": "从正文提取的学历要求",
        "text_experience": "从正文提取的经验要求",
        "publish_time": "智联SSR数据中未提供发布时间",
        "salary": "部分JD未标注薪资",
    }
    for field in fields:
        filled, rate = field_stats[field]
        note = field_notes.get(field, "")
        report.append(f"| {field} | {filled}/{n} | {rate}% | {note} |")

    report += [
        f"",
        f"---",
        f"",
        f"## 五、质量",
        f"",
        f"| 指标 | 数量 | 占比 |",
        f"|------|------|------|",
        f"| 空正文 | {len(qc['empty_detail'])} | {100*len(qc['empty_detail'])//n if n else 0}% |",
        f"| 低信息 (<200字) | {len(low_info)} | {100*len(low_info)//n if n else 0}% |",
        f"| 教育冲突 | {len(edu_conflicts)} | {100*len(edu_conflicts)//n if n else 0}% |",
        f"| 经验冲突 | {len(exp_conflicts)} | {100*len(exp_conflicts)//n if n else 0}% |",
        f"| 精确重复 (SHA256) | {len(qc['exact_dup_groups'])} 组 | - |",
        f"| 近似重复 (SimHash ≤3) | {len(qc['approx_dup_pairs'])} 对 | - |",
        f"| 无法追溯 (无URL) | {len(no_url)} | {100*len(no_url)//n if n else 0}% |",
    ]

    if lengths:
        report += [
            f"",
            f"| 正文最小长度 | {lengths[0]} 字符 |",
            f"| 正文最大长度 | {lengths[-1]} 字符 |",
            f"| 正文中位数 | {lengths[len(lengths)//2]} 字符 |",
            f"| 正文平均长度 | {sum(lengths)//len(lengths)} 字符 |",
        ]

    report += [
        f"",
        f"---",
        f"",
        f"## 六、最终可进入人工标注",
        f"",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| **accepted 数量** | **{a}** |",
        f"| review_required 数量 | {rr} |",
        f"| rejected 数量 | {rj} |",
        f"| 是否达到 ≥100 条 | {'✅ 是' if a >= 100 else '❌ 否'} |",
    ]

    if a >= 100:
        report.append(f"")
        report.append(f"已达到 ≥100 条 accepted 目标，采集停止。")
        report.append(f"请进入人工标注阶段。")

    report += [
        f"",
        f"---",
        f"",
        f"## 七、采集明细",
        f"",
        f"| # | source_id | 岗位名称 | 类别 | 公司 | 地点 | 正文长度 | 状态 |",
        f"|---|-----------|----------|------|------|------|----------|------|",
    ]
    for i, r in enumerate(records):
        title = r["job_title_raw"][:25]
        cat = r.get("job_category", "")[:10]
        company = r["company_name"][:12]
        location = r["location"][:8]
        length = len(r["detail_raw_text"])
        if r in qc["accepted"]:
            status = "accepted"
        elif r in qc["review_required"]:
            status = "review"
        else:
            status = "rejected"
        report.append(f"| {i+1} | {r['source_id']} | {title} | {cat} | {company} | {location} | {length} | {status} |")

    report += [
        f"",
        f"---",
        f"",
        f"> **注意**: 本报告仅反映本轮采集结果。未修改任何人工 Gold、Prompt、Schema 或算法。",
        f"> detail_raw_text 严格等于 responsibilities + \\\\n + requirements，不含元数据。",
        f"> SimHash 基于 responsibilities + requirements 内容，不含字段前缀。",
    ]

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print(f"Saved quality report to {REPORT_PATH}")

# ── Main ──
async def main():
    collected, stats = await collect()

    if not collected:
        print("\nERROR: No JDs collected!")
        return

    print(f"\n{'=' * 70}")
    print("Running quality checks...")
    print(f"{'=' * 70}")

    qc = run_quality_checks(collected)

    print(f"\nQuality Check Results:")
    print(f"  Total: {qc['total']}")
    print(f"  Accepted: {len(qc['accepted'])}")
    print(f"  Review required: {len(qc['review_required'])}")
    print(f"  Rejected: {len(qc['rejected'])}")
    print(f"  Empty detail: {len(qc['empty_detail'])}")
    print(f"  Empty title: {len(qc['empty_title'])}")
    print(f"  Exact dups: {len(qc['exact_dup_groups'])} groups")
    print(f"  SimHash dups: {len(qc['approx_dup_pairs'])} pairs")
    print(f"  Low information: {len([r for r in collected if r.get('low_information')])}")
    print(f"  Education conflicts: {len([r for r in collected if r.get('education_conflict')])}")
    print(f"  Experience conflicts: {len([r for r in collected if r.get('experience_conflict')])}")

    category_counts = {}
    for r in collected:
        cat = r.get("job_category", "未分类")
        category_counts[cat] = category_counts.get(cat, 0) + 1
    print(f"\nCategory distribution:")
    for cat in sorted(category_counts.keys()):
        print(f"  {cat}: {category_counts[cat]}")

    print(f"\n{'=' * 70}")
    print("Generating output files...")
    print(f"{'=' * 70}")

    generate_outputs(collected, qc, stats)

    print(f"\n{'=' * 70}")
    print(f"FINAL SUMMARY")
    print(f"{'=' * 70}")
    print(f"Raw collected: {qc['total']}")
    print(f"Accepted: {len(qc['accepted'])}")
    print(f"Review required: {len(qc['review_required'])}")
    print(f"Rejected: {len(qc['rejected'])}")
    print(f"Target >= 100 accepted: {'MET' if len(qc['accepted']) >= 100 else 'NOT MET'}")

    print(f"\nOutput files in {OUTPUT_DIR}:")
    print(f"  {RAW_PATH.name}")
    print(f"  {CLEAN_PATH.name}")
    print(f"  {CLEAN_CSV_PATH.name}")
    print(f"  {REVIEW_PATH.name}")
    print(f"  {REJECTED_PATH.name}")
    print(f"  {REPORT_PATH.name}")

if __name__ == "__main__":
    asyncio.run(main())