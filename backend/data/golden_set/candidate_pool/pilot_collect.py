"""Pilot JD collection: 20 real JDs from publicly accessible sources.

Strategy:
1. Try zhilian.com detail pages (SSR, no JS needed) using known source_ids
2. Try zhilian.com search pages to get new source_ids
3. Fall back to other sources

Constraints:
- No login bypass
- No CAPTCHA bypass
- No anti-crawling bypass
- No paywall bypass
- Public access only

Output: backend/data/golden_set/candidate_pool/real_jd_pilot_20.jsonl
"""

import asyncio
import csv
import hashlib
import json
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root to path
_BACKEND_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_BACKEND_DIR))
sys.path.insert(0, str(_BACKEND_DIR / "data"))  # crawlers package

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
JSONL_PATH = OUTPUT_DIR / "real_jd_pilot_20.jsonl"
CSV_PATH = OUTPUT_DIR / "real_jd_pilot_20.csv"
REPORT_PATH = OUTPUT_DIR / "real_jd_pilot_quality_report.md"
TARGET_COUNT = 20
DETAIL_TIMEOUT = 20
SEARCH_TIMEOUT = 30
DELAY_MIN = 7
DELAY_MAX = 10

# Known zhilian source_ids from the golden set (valid, publicly accessible)
# These are from jd_golden_100.jsonl and review candidates
KNOWN_ZHILIAN_IDS = [
    "CCL1480117890J40845576605",  # jd_012 - confirmed working
    "CC138117190J40879068905",    # jd_030 - confirmed working
    "CCL1516918430J40789275606",  # jd_013
    "CC192921310J40831468409",    # jd_015
    "CC303218880J40962177002",    # jd_021
    "CC258760917J90250298000",    # jd_025
    "CC000544460J40670242116",    # jd_036
    "CC385622410J40787862106",    # jd_053
    "CCL1480117890J40603130605",  # jd_084
    "CC135794170J41002479902",    # jd_091
    # Additional zhilian source_ids from jd_golden_100.jsonl
    "CC000283190J40710167711",    # public_002
    "CC246691810J40831043903",    # public_001
]

# Search keywords for zhilian (if we need to get new source_ids)
SEARCH_KEYWORDS = ["Python", "Java", "前端", "算法工程师", "数据分析", "大模型", "全栈", "后端"]
SEARCH_CITIES = ["北京", "上海", "深圳", "杭州", "广州"]

ZHILIAN_CITY_CODES = {
    "北京": "530", "上海": "538", "深圳": "765",
    "杭州": "539", "广州": "763", "成都": "801",
    "南京": "635", "武汉": "736",
}


def now_iso() -> str:
    return datetime.now(CST).isoformat()


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


async def try_search_zhilian(client: httpx.AsyncClient, keyword: str, city: str) -> list[dict]:
    """Try to fetch zhilian search results via SSR HTML."""
    city_code = ZHILIAN_CITY_CODES.get(city)
    if not city_code:
        return []
    url = f"https://sou.zhaopin.com/?jl={city_code}&kw={keyword}&pn=1"
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
        # Try to extract __INITIAL_STATE__ SSR data
        match = re.search(r"__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>", text, re.DOTALL)
        if not match:
            return []
        data = json.loads(match.group(1))
        # Extract job listings from the SSR data
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
            # Avoid duplicates
            if not any(r["source_id"] == number for r in results):
                results.append({
                    "source_id": number,
                    "title": title,
                    "company": obj.get("company") or obj.get("companyName") or "",
                    "location": obj.get("city") or obj.get("workCity") or city,
                    "salary": obj.get("salary") or obj.get("salaryDesc") or "",
                    "experience": obj.get("experience") or obj.get("workingExp") or "",
                    "education": obj.get("education") or obj.get("eduLevel") or "",
                    "post_date": obj.get("publishTime") or obj.get("publishDate") or "",
                })
        for v in obj.values():
            _extract_jobs_from_ssr(v, results, keyword, city)
    elif isinstance(obj, list):
        for item in obj:
            _extract_jobs_from_ssr(item, results, keyword, city)


def build_jd_record(
    source: str,
    source_id: str,
    source_url: str,
    title: str,
    company: str,
    location: str,
    salary: str,
    experience: str,
    education: str,
    description: str,
    requirements: str,
    post_date: str,
    tags: list | None = None,
) -> dict:
    """Build a standardized JD record."""
    raw_text_parts = []
    if title:
        raw_text_parts.append(f"岗位名称：{title}")
    if company:
        raw_text_parts.append(f"公司：{company}")
    if location:
        raw_text_parts.append(f"工作地点：{location}")
    if salary:
        raw_text_parts.append(f"薪资：{salary}")
    if experience:
        raw_text_parts.append(f"经验要求：{experience}")
    if education:
        raw_text_parts.append(f"学历要求：{education}")
    if tags:
        raw_text_parts.append(f"技能标签：{', '.join(tags)}")
    if description:
        raw_text_parts.append(description)
    if requirements:
        raw_text_parts.append(requirements)

    detail_raw_text = "\n".join(raw_text_parts)

    return {
        "source": source,
        "source_id": source_id,
        "source_url": source_url,
        "job_title_raw": title,
        "company_name": company,
        "location": location,
        "salary": salary,
        "experience": experience,
        "education": education,
        "publish_time": post_date,
        "crawl_time": now_iso(),
        "detail_raw_text": detail_raw_text,
        "responsibilities": description or "",
        "requirements": requirements or "",
        "tags": tags or [],
        "_sha256": hashlib.sha256(detail_raw_text.encode()).hexdigest(),
    }


def compute_simhash(text: str) -> int:
    """64-bit SimHash (same algorithm as backend/app/services/data_quality/simhash.py)."""
    if not text:
        return 0
    # Simple tokenization: split by whitespace and punctuation
    tokens = re.findall(r'[\w\u4e00-\u9fff]+', text.lower())
    if not tokens:
        return 0
    bits = 64
    weights = [0] * bits
    for token in tokens:
        h = hash(token) & 0xFFFFFFFFFFFFFFFF
        h = int(h)
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
    """Hamming distance between two 64-bit integers."""
    x = a ^ b
    dist = 0
    while x:
        dist += 1
        x &= x - 1
    return dist


async def main():
    print("=" * 60)
    print("Pilot JD Collection: 20 Real JDs")
    print(f"Start: {now_iso()}")
    print("=" * 60)

    collected = []
    failed_ids = []
    stats = {
        "total_attempted": 0,
        "detail_fetched": 0,
        "detail_empty": 0,
        "detail_failed": 0,
        "search_results": 0,
    }

    async with httpx.AsyncClient(
        timeout=DETAIL_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": UA},
    ) as client:
        # ── Phase 1: Try known zhilian source_ids ──
        print("\n[Phase 1] Trying known zhilian source_ids...")
        for source_id in KNOWN_ZHILIAN_IDS:
            if len(collected) >= TARGET_COUNT:
                break
            stats["total_attempted"] += 1
            print(f"  Fetching {source_id}...", end=" ")
            detail = await fetch_detail(client, source_id)
            if detail:
                print(f"OK (desc={len(detail['description'])} req={len(detail['requirements'])})")
                stats["detail_fetched"] += 1
                # Parse SSR data for metadata
                parsed = extract_job_detail(detail["html"])
                # Try to extract title, company, etc. from the detail page HTML
                title_match = re.search(r"<title>([^<]+)</title>", detail["html"])
                title = title_match.group(1).strip() if title_match else ""
                # Remove common suffixes
                title = re.sub(r"\s*[-–—|]\s*智联招聘.*$", "", title)
                title = re.sub(r"\s*-\s*智联招聘.*$", "", title)

                # Try to extract structured data from SSR
                ssr_match = re.search(
                    r"__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>",
                    detail["html"],
                    re.DOTALL,
                )
                company = location = salary = experience = education = ""
                post_date = ""
                tags = []
                if ssr_match:
                    try:
                        ssr_data = json.loads(ssr_match.group(1))
                        jd = (ssr_data.get("jobDetail") or {}).get("detailedPosition") or {}
                        company = jd.get("companyName") or jd.get("company") or ""
                        location = jd.get("workCity") or jd.get("city") or ""
                        salary = jd.get("salary") or jd.get("salaryDesc") or ""
                        experience = jd.get("workingExp") or jd.get("experience") or ""
                        education = jd.get("eduLevel") or jd.get("education") or ""
                        post_date = jd.get("publishTime") or jd.get("publishDate") or ""
                        # Extract tags from SSR
                        skill_list = jd.get("skillList") or jd.get("skills") or []
                        if isinstance(skill_list, list):
                            tags = [s.get("skillName", s) if isinstance(s, dict) else str(s) for s in skill_list]
                    except (json.JSONDecodeError, KeyError):
                        pass

                record = build_jd_record(
                    source="zhilian",
                    source_id=source_id,
                    source_url=f"https://www.zhaopin.com/jobdetail/{source_id}.htm",
                    title=title,
                    company=company,
                    location=location,
                    salary=salary,
                    experience=experience,
                    education=education,
                    description=detail["description"],
                    requirements=detail["requirements"],
                    post_date=post_date,
                    tags=tags,
                )
                collected.append(record)
            else:
                print("NO CONTENT")
                stats["detail_empty"] += 1
                failed_ids.append({"source_id": source_id, "reason": "no content or fetch failed"})

            await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

        # ── Phase 2: Try zhilian search pages for new source_ids ──
        if len(collected) < TARGET_COUNT:
            print(f"\n[Phase 2] Trying zhilian search pages (need {TARGET_COUNT - len(collected)} more)...")
            search_jobs = []
            for keyword in SEARCH_KEYWORDS:
                for city in SEARCH_CITIES:
                    if len(search_jobs) >= 50:  # Get enough candidates
                        break
                    print(f"  Searching {keyword} in {city}...", end=" ")
                    results = await try_search_zhilian(client, keyword, city)
                    print(f"{len(results)} results")
                    search_jobs.extend(results)
                    stats["search_results"] += len(results)
                    await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
                if len(search_jobs) >= 50:
                    break

            # Deduplicate search results
            seen_ids = {r["source_id"] for r in collected}
            for r in failed_ids:
                seen_ids.add(r["source_id"])
            new_jobs = [j for j in search_jobs if j["source_id"] not in seen_ids]

            print(f"\n  New unique source_ids from search: {len(new_jobs)}")
            for job in new_jobs:
                if len(collected) >= TARGET_COUNT:
                    break
                source_id = job["source_id"]
                stats["total_attempted"] += 1
                print(f"  Fetching {source_id} ({job.get('title', 'N/A')})...", end=" ")
                detail = await fetch_detail(client, source_id)
                if detail:
                    print(f"OK (desc={len(detail['description'])} req={len(detail['requirements'])})")
                    stats["detail_fetched"] += 1
                    record = build_jd_record(
                        source="zhilian",
                        source_id=source_id,
                        source_url=f"https://www.zhaopin.com/jobdetail/{source_id}.htm",
                        title=job.get("title", ""),
                        company=job.get("company", ""),
                        location=job.get("location", ""),
                        salary=job.get("salary", ""),
                        experience=job.get("experience", ""),
                        education=job.get("education", ""),
                        description=detail["description"],
                        requirements=detail["requirements"],
                        post_date=job.get("post_date", ""),
                    )
                    collected.append(record)
                else:
                    print("NO CONTENT")
                    stats["detail_empty"] += 1
                    failed_ids.append({"source_id": source_id, "reason": "no content"})

                await asyncio.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    # ── Save results ──
    print(f"\n{'=' * 60}")
    print(f"Collection complete: {len(collected)}/{TARGET_COUNT} JDs collected")
    print(f"Stats: {json.dumps(stats, indent=2)}")
    print(f"{'=' * 60}")

    # Save JSONL
    with open(JSONL_PATH, "w", encoding="utf-8") as f:
        for record in collected:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"\nSaved {len(collected)} records to {JSONL_PATH}")

    # Save CSV
    if collected:
        csv_fields = [
            "source", "source_id", "source_url", "job_title_raw", "company_name",
            "location", "salary", "experience", "education", "publish_time",
            "crawl_time", "responsibilities", "requirements", "tags",
        ]
        with open(CSV_PATH, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=csv_fields, extrasaction="ignore")
            writer.writeheader()
            for record in collected:
                # Convert list fields to strings for CSV
                row = dict(record)
                if isinstance(row.get("tags"), list):
                    row["tags"] = "; ".join(row["tags"])
                writer.writerow(row)
        print(f"Saved CSV to {CSV_PATH}")

    # ── Quality Checks ──
    print(f"\n{'=' * 60}")
    print("Quality Checks")
    print(f"{'=' * 60}")

    # 1. Empty detail check
    empty_detail = [r for r in collected if not r["detail_raw_text"].strip()]
    print(f"\n1. Empty detail_raw_text: {len(empty_detail)}/{len(collected)}")

    # 2. Text length check
    short_text = [r for r in collected if len(r["detail_raw_text"]) < 200]
    print(f"2. Short detail_raw_text (<200 chars): {len(short_text)}/{len(collected)}")
    length_stats = sorted([len(r["detail_raw_text"]) for r in collected])
    if length_stats:
        print(f"   Length range: {length_stats[0]} - {length_stats[-1]}, median: {length_stats[len(length_stats)//2]}")

    # 3. Job title check
    empty_title = [r for r in collected if not r["job_title_raw"].strip()]
    print(f"3. Empty job_title_raw: {len(empty_title)}/{len(collected)}")

    # 4. Responsibilities/Requirements check
    has_duties = [r for r in collected if r["responsibilities"].strip()]
    has_reqs = [r for r in collected if r["requirements"].strip()]
    print(f"4. Has responsibilities: {len(has_duties)}/{len(collected)}")
    print(f"   Has requirements: {len(has_reqs)}/{len(collected)}")

    # 5. Source URL check
    no_url = [r for r in collected if not r["source_url"].strip()]
    print(f"5. Missing source_url: {len(no_url)}/{len(collected)}")

    # 6. Duplicate URL check
    urls = [r["source_url"] for r in collected]
    dup_urls = [u for u in urls if urls.count(u) > 1]
    print(f"6. Duplicate URLs: {len(set(dup_urls))}")

    # 7. Exact duplicate text check (SHA256)
    hashes = [r["_sha256"] for r in collected]
    dup_hashes = [h for h in hashes if hashes.count(h) > 1]
    print(f"7. Exact duplicate texts (SHA256): {len(set(dup_hashes))} groups")

    # 8. SimHash approximate duplicate check
    simhash_values = [compute_simhash(r["detail_raw_text"]) for r in collected]
    approx_dup_pairs = []
    for i in range(len(simhash_values)):
        for j in range(i + 1, len(simhash_values)):
            if hamming_distance(simhash_values[i], simhash_values[j]) <= 3:
                approx_dup_pairs.append((i, j))
    print(f"8. Approximate duplicates (SimHash Hamming <= 3): {len(approx_dup_pairs)} pairs")

    # 9. Time field completeness
    has_publish = [r for r in collected if r["publish_time"].strip()]
    has_crawl = [r for r in collected if r["crawl_time"].strip()]
    print(f"9. Has publish_time: {len(has_publish)}/{len(collected)}")
    print(f"   Has crawl_time: {len(has_crawl)}/{len(collected)}")

    # 10. Field completeness
    fields = ["job_title_raw", "source_url", "detail_raw_text", "responsibilities",
              "requirements", "education", "experience", "publish_time"]
    print(f"\n10. Field completeness:")
    for field in fields:
        filled = [r for r in collected if r.get(field, "").strip()]
        print(f"    {field}: {len(filled)}/{len(collected)} ({100*len(filled)//len(collected)}%)")

    # 11. Source distribution
    sources = {}
    for r in collected:
        s = r["source"]
        sources[s] = sources.get(s, 0) + 1
    print(f"\n11. Source distribution:")
    for s, c in sources.items():
        print(f"    {s}: {c}")

    # 12. Label candidates for manual annotation
    annotatable = []
    for r in collected:
        if (r["detail_raw_text"].strip() and
            r["job_title_raw"].strip() and
            r["source_url"].strip() and
            (r["responsibilities"].strip() or r["requirements"].strip())):
            # Check not obvious duplicate
            is_dup = False
            rh = r["_sha256"]
            for other in annotatable:
                if other["_sha256"] == rh:
                    is_dup = True
                    break
            if not is_dup:
                annotatable.append(r)

    print(f"\n12. Candidates for manual annotation: {len(annotatable)}/{len(collected)}")
    print(f"    Criteria: real JD text + title + source_url + duties/reqs + non-duplicate")

    # ── Generate Quality Report ──
    report_lines = [
        "# JD Pilot Collection Quality Report",
        f"",
        f"**Collection Time**: {now_iso()}",
        f"**Target**: {TARGET_COUNT} real JDs",
        f"**Collected**: {len(collected)}",
        f"",
        f"## Collection Results",
        f"",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Target count | {TARGET_COUNT} |",
        f"| Successfully collected (完整正文) | {len(collected)} |",
        f"| Incomplete (正文不完整) | {stats['detail_empty']} |",
        f"| Failed (无法获取) | {stats['detail_failed']} |",
        f"",
        f"## Source Distribution",
        f"",
        f"| Source | Count |",
        f"|--------|-------|",
    ]
    for s, c in sources.items():
        report_lines.append(f"| {s} | {c} |")
    if not sources:
        report_lines.append("| None | 0 |")

    report_lines += [
        f"",
        f"## Field Completeness",
        f"",
        f"| Field | Complete | Rate |",
        f"|-------|----------|------|",
    ]
    for field in fields:
        filled = len([r for r in collected if r.get(field, "").strip()])
        rate = f"{100*filled//len(collected)}%" if collected else "N/A"
        report_lines.append(f"| {field} | {filled}/{len(collected)} | {rate} |")

    report_lines += [
        f"",
        f"## Duplicate Check",
        f"",
        f"| Type | Count |",
        f"|------|-------|",
        f"| Exact duplicates (SHA256) | {len(set(dup_hashes))} groups |",
        f"| Approximate duplicates (SimHash ≤3) | {len(approx_dup_pairs)} pairs |",
        f"| Duplicate URLs | {len(set(dup_urls))} |",
        f"",
        f"## Text Quality",
        f"",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Empty detail_raw_text | {len(empty_detail)} |",
        f"| Short detail (<200 chars) | {len(short_text)} |",
    ]
    if length_stats:
        report_lines.append(f"| Min length | {length_stats[0]} chars |")
        report_lines.append(f"| Max length | {length_stats[-1]} chars |")
        report_lines.append(f"| Median length | {length_stats[len(length_stats)//2]} chars |")

    report_lines += [
        f"",
        f"## Manual Annotation Candidates",
        f"",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Candidates | {len(annotatable)} |",
        f"| Criteria | real JD text + title + source_url + duties/reqs + non-duplicate |",
        f"",
        f"## Failed Source IDs",
        f"",
    ]
    if failed_ids:
        report_lines.append("| Source ID | Reason |")
        report_lines.append("|-----------|--------|")
        for f_item in failed_ids:
            report_lines.append(f"| {f_item['source_id']} | {f_item['reason']} |")
    else:
        report_lines.append("None")

    report_lines += [
        f"",
        f"## Conclusion",
        f"",
    ]
    success_rate = len(collected) / max(stats["total_attempted"], 1) * 100
    if len(collected) >= TARGET_COUNT * 0.8:
        report_lines.append(f"Success rate: {success_rate:.1f}% ({len(collected)}/{stats['total_attempted']})")
        report_lines.append(f"Collection meets the 80% threshold. Ready to expand to 120-150 JDs.")
    else:
        report_lines.append(f"**WARNING**: Success rate is only {success_rate:.1f}% ({len(collected)}/{stats['total_attempted']}).")
        report_lines.append(f"Below the 80% threshold. Do NOT expand collection. Analyze data source issues first.")
        report_lines.append(f"")
        report_lines.append(f"Potential issues:")
        report_lines.append(f"- Zhilian detail pages may require JavaScript rendering")
        report_lines.append(f"- Source IDs may be expired")
        report_lines.append(f"- Anti-crawling measures may be blocking requests")

    report_lines.append(f"")
    report_lines.append(f"## Next Steps")
    report_lines.append(f"")
    if len(collected) >= TARGET_COUNT * 0.8:
        report_lines.append(f"1. Review the {len(annotatable)} annotation candidates")
        report_lines.append(f"2. Propose expansion plan to 120-150 JDs")
        report_lines.append(f"3. Consider adding BOSS direct hire or other sources")
    else:
        report_lines.append(f"1. Investigate why most zhilian detail pages return no content")
        report_lines.append(f"2. Try browser-based fetching (Playwright) for zhilian detail pages")
        report_lines.append(f"3. Consider alternative data sources")

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines))
    print(f"\nSaved quality report to {REPORT_PATH}")

    return collected, annotatable, stats


if __name__ == "__main__":
    asyncio.run(main())