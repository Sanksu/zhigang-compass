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
# 默认 CDP 端点（可由环境变量 BOSS_CDP_URL 覆盖，支持局域网内容器浏览器）
DEFAULT_CDP_URL = os.environ.get("BOSS_CDP_URL", f"http://127.0.0.1:{DEFAULT_CDP_PORT}")


async def crawl(keyword: str, city: str, max_pages: int = 2, cdp_url: str = DEFAULT_CDP_URL) -> int:
    """通过 CDP 连接已启动的 Chrome/Edge，拦截 Monster 内部 API XHR 采集岗位。

    前置条件：用户需先启动带 CDP 的 Chrome/Edge 并完成 DataDome challenge。
    可复用 BOSS 的隔离 profile（setup_boss_chrome.py），但建议用独立 profile
    避免冲突。简单做法：直接复用 BOSS 的 Edge（已绕过 DataDome）。

    Args:
        keyword: 搜索关键词
        city: 城市（如 "New York"）
        max_pages: 最大页数
        cdp_url: CDP 调试端点

    Returns:
        采集到的岗位数
    """
    from playwright.async_api import async_playwright

    items = []
    all_jobs_data = []

    async with async_playwright() as p:
        # CDP 连接已启动的浏览器（绕过 DataDome 的 headless 检测）
        try:
            browser = await p.chromium.connect_over_cdp(cdp_url)
            log(f"✅ CDP 连接成功: {cdp_url}（浏览器版本: {browser.version}）")
        except Exception as e:
            log(f"❌ CDP 连接失败（{cdp_url}）: {e}")
            log(f"   请先运行 setup_boss_chrome.py 启动带 CDP 的 Chrome/Edge")
            return 0

        # 隔离：新建独立 context 并复制主 context 的 cookies（保留 DataDome 验证），
        # 爬虫导航只发生在隔离 context 内，不触碰用户正在浏览的页面
        context = await browser.new_context()
        if browser.contexts:
            try:
                _cookies = await browser.contexts[0].cookies()
                if _cookies:
                    await context.add_cookies(_cookies)
                    log(f"ℹ️ 已复制 {len(_cookies)} 个 cookies 到隔离 context")
                else:
                    log(f"⚠️ 主 context 无 cookies（DataDome 验证可能未完成）")
            except Exception as e:
                log(f"⚠️ 复制 cookies 到隔离 context 失败: {e}")
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
                except Exception as e:
                    log(f"  ⚠️ 拦截响应解析失败: {e}")

        page.on("response", handle_response)

        for page_num in range(1, max_pages + 1):
            log(f"=== 采集第 {page_num}/{max_pages} 页 ===")

            url = f"https://www.monster.com/jobs/search?{urlencode({'q': keyword, 'where': city, 'page': page_num})}"

            try:
                # networkidle 在隔离 context 的 DataDome 场景下可能长时间不空闲（持续请求），
                # 改用 domcontentloaded + 等待 SPA 渲染，再靠下方的 API 响应轮询兜底
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(5000)
                log(f"  页面已加载 | 当前 URL: {page.url} | 标题: {await page.title()}")
            except Exception as e:
                log(f"  导航失败: {e}")
                try:
                    log(f"  当前 URL: {page.url} | 标题: {await page.title()}")
                except Exception:
                    pass
                if captured_api_responses:
                    log(f"  导航超时但已有 API 响应，继续处理")
                else:
                    continue

            log("  等待 API 响应...")
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 20
            while not captured_api_responses and loop.time() < deadline:
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

        # 描述（提前赋值，供下游 salary/experience/education 正则提取）
        description = posting.get("description", "")
        if isinstance(description, dict):
            description = description.get("text", "") or json.dumps(description, ensure_ascii=False)

        # enrichments（提前赋值，供下游 skills 提取）
        enrichments = job.get("enrichments", {}) or {}

        # 薪资/经验/学历：Monster API 不返回结构化字段，从 description 正则提取
        import re
        description_text = str(description) if description else ""

        salary_str = ""
        if description_text:
            m = re.search(r"\$([\d,]+)\s*(?:-\s*\$([\d,]+))?\s*(?:/yr|/year|per year|annually)", description_text, re.IGNORECASE)
            if m:
                if m.group(2):
                    salary_str = f"${m.group(1)}-${m.group(2)} /year"
                else:
                    salary_str = f"${m.group(1)} /year"

        # 经验要求：从 description 提取 "X+ years" 或 "X to Y years"
        experience_str = ""
        if description_text:
            m = re.search(r"(\d+)\+?\s*(?:to\s*(\d+)\+?\s*)?Years?", description_text, re.IGNORECASE)
            if m:
                if m.group(2):
                    experience_str = f"{m.group(1)}-{m.group(2)} Years"
                else:
                    experience_str = f"{m.group(1)}+ Years"

        # 学历要求：从 description 提取关键词
        education_str = ""
        if description_text:
            desc_lower = description_text.lower()
            for keyword, label in [
                ("phd", "PhD"),
                ("master", "Master"),
                ("bachelor", "Bachelor"),
                ("degree", "Degree"),
            ]:
                if keyword in desc_lower:
                    education_str = label
                    break

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
            "experience_range": experience_str,
            "education": education_str,
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
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL,
                        help="CDP 调试端点（默认 http://127.0.0.1:9222，支持局域网内容器浏览器）")
    args = parser.parse_args()

    log(f"CDP 端点: {args.cdp_url}")
    count = asyncio.run(crawl(args.keyword, args.city, args.max_pages, args.cdp_url))
    sys.exit(0 if count > 0 else 1)


if __name__ == "__main__":
    main()
