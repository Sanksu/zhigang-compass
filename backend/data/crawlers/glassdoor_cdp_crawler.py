"""Glassdoor CDP 采集脚本（独立运行，避免与 Scrapy Twisted 事件循环冲突）。

被 spiders/glassdoor.py 通过 subprocess 调用，输出 JSONL 到 stdout。
状态/错误日志输出到 stderr。

核心策略（2026-07-29 重构）：
- JobSpy location 接口 400（Glassdoor 限制 location 查询）
- Glassdoor 搜索页是 SSR 的，初始 30 条岗位直接嵌入 DOM + JSON-LD
- 用 CDP 连接已启动的真实 Chrome/Edge（复用真实指纹 + 已绕过 Cloudflare）
- 直接从 DOM 提取岗位卡片（data-test 属性稳定，不依赖 CSS Modules 哈希类名）
- 详情页描述通过 JSON-LD 的 JobPosting schema 提取

前置条件：
- 用户需先启动带 CDP 的 Chrome/Edge（python -m crawlers.setup_boss_chrome）
- 浏览器需配置系统代理访问 glassdoor（Clash/V2Ray）
- 在浏览器中先访问 glassdoor.com 完成一次 Cloudflare 验证

DOM 结构（2026-07-29 实测）：
- 列表项: li[data-jobid][data-test="jobListing"]
- 标题: a[data-test="job-title"]
- 公司: 隐藏在卡片文本中（需用 DOM 结构定位）
- 地点: div[data-test="emp-location"]
- 薪资: div[data-test="detailSalary"]
- 描述片段: div[data-test="descSnippet"]
- 发布时间: div[data-test="job-age"]
- 详情链接: a[data-test="job-link"] 的 href
"""

import argparse
import asyncio
import json
import os
import sys
from urllib.parse import urlencode, urljoin


def log(msg: str):
    """日志输出到 stderr，不污染 stdout 的 JSONL。"""
    print(msg, file=sys.stderr, flush=True)


# 默认 CDP 端口（与 BOSS/Monster 共用，同一时刻只能一个爬虫用）
DEFAULT_CDP_PORT = 9222
# 默认 CDP 端点（可由环境变量 BOSS_CDP_URL 覆盖，支持局域网内容器浏览器）
DEFAULT_CDP_URL = os.environ.get("BOSS_CDP_URL", f"http://127.0.0.1:{DEFAULT_CDP_PORT}")


# 从 DOM 提取岗位卡片的 JS 表达式
# 策略：优先用 JSON-LD（结构化最稳定），DOM 作为补充获取 data-jobid
EXTRACT_JOBS_JS = """
() => {
    const result = [];

    // 1. 从 JSON-LD 提取 ItemList（schema.org 标准格式，字段最完整）
    let jsonldItems = [];
    for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
        try {
            const text = script.textContent || '';
            if (text.includes('"ItemList"') || text.includes('"itemListElement"')) {
                const data = JSON.parse(text);
                if (data && data.itemListElement) {
                    jsonldItems = data.itemListElement;
                    break;
                }
            }
        } catch (e) {}
    }

    // 2. 从 DOM 提取 data-jobid 列表（用于补全 source_id）
    const domCards = Array.from(document.querySelectorAll('li[data-jobid][data-test="jobListing"]'));
    const domJobIds = domCards.map(c => c.getAttribute('data-jobid'));

    // 3. 优先用 JSON-LD 数据（含 url/title），DOM 仅作 fallback
    if (jsonldItems.length > 0) {
        for (let i = 0; i < jsonldItems.length; i++) {
            const item = jsonldItems[i];
            // ListItem 格式: {position, name, url}
            const title = item.name || '';
            const url = item.url || '';
            if (!title || !url) continue;

            // 从 URL 提取 job id
            // URL 格式: https://www.glassdoor.com.hk/job-listing/software-engineer-jobs-SRCH_KO0,15_KE16,25.htm?jl=123456789
            const jlMatch = url.match(/[?&]jl=(\\d+)/);
            const jobId = jlMatch ? jlMatch[1] : (domJobIds[i] || '');

            result.push({
                source_id: jobId,
                source_url: url,
                title: title,
                company: '',  // JSON-LD ItemList 不含公司名，从 DOM 补
                location: '',
                salary: '',
                description: '',
                age: '',
                from_jsonld: true,
            });
        }
    }

    // 4. 如果 JSON-LD 提取失败，从 DOM 卡片提取
    if (result.length === 0) {
        for (const card of domCards) {
            const titleEl = card.querySelector('a[data-test="job-title"]');
            const locEl = card.querySelector('div[data-test="emp-location"]');
            const salaryEl = card.querySelector('div[data-test="detailSalary"]');
            const descEl = card.querySelector('div[data-test="descSnippet"]');
            const ageEl = card.querySelector('div[data-test="job-age"]');
            const linkEl = card.querySelector('a[data-test="job-link"]');

            const title = titleEl ? titleEl.innerText.trim() : '';
            const jobId = card.getAttribute('data-jobid') || '';

            // 公司名：从卡片的第一个文本节点提取（位于标题之前）
            // DOM 结构: EmployerName > Rating > Title > Location > Salary > Desc
            // 公司通常在卡片文本的第一行
            const fullText = card.innerText.split('\\n').map(s => s.trim()).filter(Boolean);
            const company = fullText[0] || '';

            // 详情 URL：link 的 href 或构造
            let detailUrl = '';
            if (linkEl) {
                detailUrl = linkEl.href || '';
            }
            if (!detailUrl && jobId) {
                detailUrl = `https://www.glassdoor.com/job-listing/j?jl=${jobId}`;
            }

            if (title && jobId) {
                result.push({
                    source_id: jobId,
                    source_url: detailUrl,
                    title: title,
                    company: company,
                    location: locEl ? locEl.innerText.trim() : '',
                    salary: salaryEl ? salaryEl.innerText.trim() : '',
                    description: descEl ? descEl.innerText.trim() : '',
                    age: ageEl ? ageEl.innerText.trim() : '',
                    from_jsonld: false,
                });
            }
        }
    } else {
        // 5. JSON-LD 提取成功，但需用 DOM 补充 company/location/salary/desc/age
        for (let i = 0; i < result.length; i++) {
            const card = domCards[i];
            if (!card) continue;
            const locEl = card.querySelector('div[data-test="emp-location"]');
            const salaryEl = card.querySelector('div[data-test="detailSalary"]');
            const descEl = card.querySelector('div[data-test="descSnippet"]');
            const ageEl = card.querySelector('div[data-test="job-age"]');

            // 公司名：从卡片的第一个文本节点提取
            const fullText = card.innerText.split('\\n').map(s => s.trim()).filter(Boolean);
            if (!result[i].company) result[i].company = fullText[0] || '';
            if (!result[i].location) result[i].location = locEl ? locEl.innerText.trim() : '';
            if (!result[i].salary) result[i].salary = salaryEl ? salaryEl.innerText.trim() : '';
            if (!result[i].description) result[i].description = descEl ? descEl.innerText.trim() : '';
            if (!result[i].age) result[i].age = ageEl ? ageEl.innerText.trim() : '';
        }
    }

    return result;
}
"""


async def crawl(keyword: str, city: str, max_pages: int = 2, cdp_url: str = DEFAULT_CDP_URL) -> int:
    """通过 CDP 连接已启动的 Chrome/Edge，从 SSR 页面 DOM 提取岗位。

    前置条件：
    - 用户已运行 setup_boss_chrome.py 启动带 CDP 的浏览器
    - 浏览器已配置系统代理（Clash/V2Ray）访问 glassdoor
    - 浏览器中已访问过 glassdoor.com 通过 Cloudflare 验证

    Args:
        keyword: 搜索关键词
        city: 城市（如 "New York"）
        max_pages: 最大页数
        cdp_url: CDP 调试端点

    Returns:
        采集到的岗位数
    """
    from playwright.async_api import async_playwright

    all_jobs_data = []

    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(cdp_url)
        except Exception as e:
            log(f"❌ CDP 连接失败（{cdp_url}）: {e}")
            log(f"   请先运行 setup_boss_chrome.py 启动带 CDP 的 Chrome/Edge")
            return 0

        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await context.new_page()

        # 先导航到 glassdoor.com 首页建立会话，并确认重定向后的实际域（.com → .com.hk）
        try:
            await page.goto("https://www.glassdoor.com/", wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            log(f"导航到首页失败: {e}")

        # 通过 typeahead 接口动态解析城市 → locId（避免硬编码城市映射，支持任意城市）
        loc_params = {}
        try:
            term = json.dumps(city)
            loc_params = await page.evaluate(
                f"""async () => {{
                    const term = {term};
                    const r = await fetch(
                        location.origin + '/findPopularLocationAjax.htm?maxLocationsToReturn=5&term=' + encodeURIComponent(term),
                        {{credentials: 'include'}}
                    );
                    const arr = await r.json();
                    if (Array.isArray(arr) && arr.length > 0) {{
                        return {{id: arr[0].locationId, type: arr[0].locationType || 'C', name: arr[0].longName || ''}};
                    }}
                    return null;
                }}"""
            )
        except Exception as e:
            log(f"城市解析失败（将按全国范围搜索）: {e}")

        if loc_params and loc_params.get("id"):
            log(f"城市 '{city}' → locId={loc_params['id']} type={loc_params.get('type')}")
        else:
            log(f"⚠️ 未解析到城市 '{city}' 的 locId，将按全国范围搜索")

        for page_num in range(1, max_pages + 1):
            log(f"=== 采集第 {page_num}/{max_pages} 页 ===")

            # Glassdoor 搜索 URL；未解析到 locId 时不带位置参数（全国搜索）
            params = {
                "sc.keyword": keyword,
                "page": page_num,
            }
            if loc_params and loc_params.get("id"):
                params["locT"] = loc_params.get("type", "C")
                params["locId"] = str(loc_params["id"])
            url = f"https://www.glassdoor.com/Job/jobs.htm?{urlencode(params)}"

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                log(f"  导航失败: {e}")
                continue

            # 等待岗位卡片渲染
            try:
                await page.wait_for_selector('li[data-jobid][data-test="jobListing"]', timeout=15000)
            except Exception as e:
                log(f"  ⚠️ 等待岗位卡片超时: {e}")
                try:
                    title = await page.title()
                    log(f"  页面标题: {title}")
                    if "blocked" in title.lower() or "access" in title.lower():
                        log(f"  ❌ 疑似被 Cloudflare 拦截，请在浏览器中先访问 glassdoor.com 完成验证")
                        break
                except Exception:
                    pass
                continue

            # 额外等待 JSON-LD 渲染完成
            await page.wait_for_timeout(2000)

            # 从 DOM 提取岗位
            try:
                jobs = await page.evaluate(EXTRACT_JOBS_JS)
            except Exception as e:
                log(f"  ❌ DOM 提取失败: {e}")
                continue

            log(f"  第 {page_num} 页提取 {len(jobs)} 条岗位")
            if jobs:
                log(f"  样本: {json.dumps(jobs[0], ensure_ascii=False)[:200]}")

            if not jobs:
                log(f"  第 {page_num} 页无岗位，结束采集")
                break

            all_jobs_data.extend(jobs)

            if page_num < max_pages:
                log("  翻页间隔 5 秒...")
                await asyncio.sleep(5)

        # CDP 模式下不关闭 browser（避免关闭用户的浏览器）
        await page.close()

    # 输出 JSONL
    count = 0
    for job in all_jobs_data:
        item = _map_job_to_item(job)
        if item:
            print(json.dumps(item, ensure_ascii=False), flush=True)
            count += 1

    log(f"✅ 采集完成: kw={keyword} city={city} count={count}")
    return count


def _map_job_to_item(job: dict) -> dict | None:
    """将 DOM 提取的岗位数据映射为统一 Item 格式。"""
    try:
        job_id = str(job.get("source_id", ""))
        title = str(job.get("title", ""))
        if not job_id or not title:
            return None

        description = str(job.get("description", ""))

        # 经验要求：从描述片段正则提取（Glassdoor 列表页 DOM 不稳定）
        experience = ""
        if description:
            import re
            m = re.search(r"(\d+)\+?\s*(?:to\s*(\d+)\+?\s*)?Years?", description, re.IGNORECASE)
            if m:
                if m.group(2):
                    experience = f"{m.group(1)}-{m.group(2)} Years"
                else:
                    experience = f"{m.group(1)}+ Years"

        # 学历要求：从描述片段提取关键词
        education = ""
        if description:
            desc_lower = description.lower()
            for keyword, label in [
                ("phd", "PhD"),
                ("master", "Master"),
                ("ms ", "MS"),
                ("ms.", "MS"),
                ("bachelor", "Bachelor"),
                ("bs ", "BS"),
                ("bs.", "BS"),
                ("degree", "Degree"),
            ]:
                if keyword in desc_lower:
                    education = label
                    break

        return {
            "id": f"gd-{job_id}",
            "title": title,
            "company": str(job.get("company", "")),
            "location": str(job.get("location", "")),
            "salary": str(job.get("salary", "")),
            "description": description,  # 列表页仅描述片段
            "url": str(job.get("source_url", "")),
            "is_remote": False,
            "skills": [],
            "date_posted": str(job.get("age", "")),
            "company_industry": "",
            "experience_range": experience,
            "education": education,
            "raw": job,
        }
    except Exception as e:
        log(f"⚠️ 字段映射失败: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Glassdoor CDP 采集脚本（SSR DOM 提取）")
    parser.add_argument("--keyword", required=True, help="搜索关键词")
    parser.add_argument("--city", required=True, help="城市（如 New York）")
    parser.add_argument("--max-pages", type=int, default=2, help="最大页数")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL,
                        help="CDP 调试端点（默认 http://127.0.0.1:9222，支持局域网内容器浏览器）")
    args = parser.parse_args()

    log(f"CDP 端点: {args.cdp_url}")
    count = asyncio.run(crawl(args.keyword, args.city, args.max_pages, args.cdp_url))
    sys.exit(0 if count > 0 else 1)


if __name__ == "__main__":
    main()
