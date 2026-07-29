"""Monster 采集脚本（独立运行，避免与 Scrapy Twisted 事件循环冲突）。

被 spiders/monster.py 通过 subprocess 调用，输出 JSONL 到 stdout。
状态/错误日志输出到 stderr。

核心策略（参考 shahidirfan100/Monster-Job-Scraper，2026-07-28 更新）：
- Monster 用 DataDome 反爬，直连 API 被拦截（403），headless 浏览器也被检测
- 用 CDP 连接已启动的真实 Chrome/Edge（复用真实指纹 + 登录态）
- 监听 response 事件拦截内部 API XHR，直接拿 JSON
- 不解析脆弱的 HTML 选择器，字段更丰富稳定

参考项目：https://github.com/shahidirfan100/Monster-Job-Scraper
"""

import argparse
import asyncio
import json
import os
import sys
import traceback
from urllib.parse import urlencode


def log(msg: str):
    """日志输出到 stderr，不污染 stdout 的 JSONL。"""
    print(msg, file=sys.stderr, flush=True)


# Monster 内部 API 端点（前端公开 key，非私密）
API_HOST = "appsapi.monster.io"
SEARCH_API_PATH = "/jobs-svx-service/v2/monster/search-jobs/samsearch/en-US"

# 默认 CDP 端口（与 BOSS 共用，同一时刻只能一个爬虫用）
DEFAULT_CDP_PORT = 9222


async def crawl(keyword: str, city: str, max_pages: int = 2, cdp_port: int = DEFAULT_CDP_PORT) -> int:
    """通过 CDP 连接已启动的 Chrome/Edge，拦截 Monster 内部 API XHR 采集岗位。

    前置条件：用户需先启动带 CDP 的 Chrome/Edge 并完成 DataDome challenge。
    可复用 BOSS 的隔离 profile（setup_boss_chrome.py），但建议用独立 profile
    避免冲突。简单做法：直接复用 BOSS 的 Edge（已绕过 DataDome）。

    Args:
        keyword: 搜索关键词
        city: 城市（如 "New York"）
        max_pages: 最大页数
        cdp_port: CDP 端口

    Returns:
        采集到的岗位数
    """
    from playwright.async_api import async_playwright

    items = []
    all_jobs_data = []

    async with async_playwright() as p:
        # CDP 连接已启动的浏览器（绕过 DataDome 的 headless 检测）
        try:
            browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
        except Exception as e:
            log(f"❌ CDP 连接失败（端口 {cdp_port}）: {e}")
            log(f"   请先运行 setup_boss_chrome.py 启动带 CDP 的 Chrome/Edge")
            return 0

        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await context.new_page()

        # 监听所有响应，拦截 Monster 内部 API
        captured_api_responses = []

        async def handle_response(response):
            url = response.url
            if API_HOST in url and SEARCH_API_PATH in url:
                try:
                    if response.request.method == "POST" and response.ok:
                        body = await response.json()
                        # Monster API 实际字段：jobResults（不是 jobAds.jobAds）
                        jobs = body.get("jobResults", [])
                        captured_api_responses.append(body)
                        log(f"  ✅ 拦截到搜索 API 响应: {len(jobs)} 条岗位 (totalSize={body.get('totalSize')})")
                        if jobs:
                            log(f"  样本字段: {list(jobs[0].keys())[:15]}")
                except Exception as e:
                    log(f"  ⚠️ 拦截响应解析失败: {e}")

        page.on("response", handle_response)

        for page_num in range(1, max_pages + 1):
            log(f"=== 采集第 {page_num}/{max_pages} 页 ===")

            url = f"https://www.monster.com/jobs/search?{urlencode({'q': keyword, 'where': city, 'page': page_num})}"

            try:
                await page.goto(url, wait_until="networkidle", timeout=60000)
            except Exception as e:
                log(f"  导航失败: {e}")
                if captured_api_responses:
                    log(f"  导航超时但已有 API 响应，继续处理")
                else:
                    continue

            log("  等待 API 响应...")
            deadline = asyncio.get_event_loop().time() + 20
            while not captured_api_responses and asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(0.5)

            if not captured_api_responses:
                log(f"  ⚠️ 第 {page_num} 页未拦截到 API 响应")
                try:
                    title = await page.title()
                    log(f"  页面标题: {title}")
                    # 检查是否在 DataDome challenge 页
                    if "blocked" in title.lower() or "access" in title.lower():
                        log(f"  ❌ 被 DataDome 拦截")
                        break
                except Exception:
                    pass
                continue

            api_data = captured_api_responses[-1]
            captured_api_responses.clear()

            # Monster API 实际字段：jobResults
            job_ads = api_data.get("jobResults", [])
            log(f"  第 {page_num} 页岗位数: {len(job_ads)}")

            if not job_ads:
                log(f"  第 {page_num} 页无岗位，结束采集")
                log(f"  API 响应顶层 keys: {list(api_data.keys())}")
                break

            all_jobs_data.extend(job_ads)

            if page_num < max_pages:
                log("  翻页间隔 5 秒...")
                await asyncio.sleep(5)

        # CDP 模式下不关闭 browser（避免关闭用户的浏览器）
        # await browser.close()

    # 输出所有岗位为 JSONL
    for job in all_jobs_data:
        item = _map_job_to_item(job)
        if item:
            print(json.dumps(item, ensure_ascii=False), flush=True)
            items.append(item)

    log(f"✅ 采集完成: kw={keyword} city={city} count={len(items)}")
    return len(items)


def _map_job_to_item(job: dict) -> dict:
    """将 Monster API 响应字段映射为统一 Item 格式。

    Monster API 实际结构（2026-07-29 实测）：
    - 顶层有 jobId、jobPosting（schema.org JobPosting）、enrichments
    - jobPosting 内含 title/description/hiringOrganization.name/jobLocation
    - enrichments 内含 skills、normalizedSalary、normalizedTitles
    """
    try:
        job_id = job.get("jobId", "") or job.get("id", "")

        # 岗位信息在 jobPosting 子对象（schema.org JobPosting 格式）
        posting = job.get("jobPosting", {}) or job.get("normalizedJobPosting", {})
        title = posting.get("title", "") or job.get("jobTitle", "")

        # 公司名
        org = posting.get("hiringOrganization", {})
        company = org.get("name", "") if isinstance(org, dict) else ""

        # 地点（jobLocation 是数组）
        location_str = ""
        job_locs = posting.get("jobLocation", [])
        if isinstance(job_locs, list) and job_locs:
            addr = job_locs[0].get("address", {}) if isinstance(job_locs[0], dict) else {}
            location_str = ", ".join(filter(None, [
                addr.get("addressLocality", ""),
                addr.get("addressRegion", ""),
                addr.get("addressCountry", ""),
            ]))
        elif isinstance(job_locs, dict):
            addr = job_locs.get("address", {})
            location_str = ", ".join(filter(None, [
                addr.get("addressLocality", ""),
                addr.get("addressRegion", ""),
                addr.get("addressCountry", ""),
            ]))

        # 薪资（enrichments.normalizedSalary）
        salary_str = ""
        enrichments = job.get("enrichments", {}) or {}
        norm_sal = enrichments.get("normalizedSalary", {})
        if isinstance(norm_sal, dict):
            currency = norm_sal.get("currencyCode", {}).get("name", "USD") if isinstance(norm_sal.get("currencyCode"), dict) else "USD"
            # normalizedSalary 无具体金额，保留字段

        # 技能（enrichments.skills.scoredExtractions）
        skills_list = []
        skills_data = enrichments.get("skills", {})
        if isinstance(skills_data, dict):
            scored = skills_data.get("scoredExtractions", [])
            for group in scored:
                if isinstance(group, dict):
                    for ext in group.get("extractions", []):
                        if isinstance(ext, dict) and ext.get("value"):
                            skills_list.append(str(ext["value"]))

        # 标签
        tags = []
        if posting.get("employmentType"):
            et = posting["employmentType"]
            if isinstance(et, list):
                tags.extend([str(t) for t in et])
            else:
                tags.append(str(et))
        if job.get("jobType"):
            tags.append(str(job["jobType"]))
        tags.extend(skills_list)

        # 描述
        description = posting.get("description", "")
        if isinstance(description, dict):
            description = description.get("text", "") or json.dumps(description, ensure_ascii=False)

        # URL
        url = job.get("canonicalUrl", "") or posting.get("url", "") or f"https://www.monster.com/job/{job_id}"
        apply_url = job.get("apply", {}).get("applyUrl", "") if isinstance(job.get("apply"), dict) else ""
        if apply_url:
            url = apply_url  # 优先用 applyUrl（实际可申请的链接）

        # 日期
        date_posted = posting.get("datePosted", "") or job.get("formattedDate", "")

        return {
            "id": str(job_id),
            "title": str(title),
            "company": str(company) if company else "",
            "location": location_str,
            "salary": salary_str,
            "description": str(description),
            "url": url,
            "job_type": str(job.get("jobType", "")),
            "is_remote": False,  # Monster API 不直接返回此字段
            "skills": skills_list,
            "date_posted": str(date_posted),
            "company_industry": "",
            "experience_range": "",
            "raw": job,
        }
    except Exception as e:
        log(f"⚠️ 字段映射失败: {e}, job keys: {list(job.keys())[:10]}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Monster 采集脚本（CDP + XHR 拦截）")
    parser.add_argument("--keyword", required=True, help="搜索关键词")
    parser.add_argument("--city", required=True, help="城市")
    parser.add_argument("--max-pages", type=int, default=2, help="最大页数")
    parser.add_argument("--cdp-port", type=int, default=int(os.environ.get("BOSS_CDP_PORT", DEFAULT_CDP_PORT)),
                        help=f"CDP 端口（默认 {DEFAULT_CDP_PORT}，复用 BOSS 的隔离 Chrome）")
    args = parser.parse_args()

    log(f"CDP 端口: {args.cdp_port}")
    count = asyncio.run(crawl(args.keyword, args.city, args.max_pages, args.cdp_port))
    sys.exit(0 if count > 0 else 1)


if __name__ == "__main__":
    main()
