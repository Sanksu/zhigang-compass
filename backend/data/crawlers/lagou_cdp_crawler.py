"""拉勾网 CDP 采集脚本（独立运行，避免与 Scrapy Twisted 事件循环冲突）。

被 spiders/lagou.py 通过 subprocess 调用，输出 JSONL 到 stdout。
状态/错误日志输出到 stderr。

核心策略（2026-07-29 重构）：
- 旧方案：Playwright 直接渲染被阿里云 WAF 拦截（滑动验证页面）
- 新方案：CDP 连接已启动的真实 Chrome/Edge（复用真实指纹 + 已通过 WAF + 已登录）
- 在页面上下文中用 page.evaluate(fetch) 调用内部 API positionAjax.json
- Cookie 自动带上（包括 X-Anit-Token），绕过 WAF

前置条件（重要！）：
1. 启动 CDP 浏览器：python -m crawlers.setup_boss_chrome
2. 在浏览器中访问 https://www.lagou.com/
3. 完成滑动验证（阿里云 WAF）
4. 登录账号（扫码/手机号）
5. 保持浏览器开启，爬虫通过 CDP 复用登录态

拉勾内部 API：
- 搜索: POST https://www.lagou.com/jobs/positionAjax.json?px=default&city={city}&needAddtionalResult=false
- Body: first=true&pn={page}&kd={keyword} (application/x-www-form-urlencoded)
- 需先访问搜索页获取 Cookie（X-Anit-Token 等）

参考：BOSS 直聘 CDP 方案（boss_cdp_crawler.py）
"""

import argparse
import asyncio
import json
import os
import random
import sys
from urllib.parse import quote, urlencode


def log(msg: str):
    """日志输出到 stderr，不污染 stdout 的 JSONL。"""
    print(msg, file=sys.stderr, flush=True)


# 拉勾内部搜索 API
LAGOU_API_URL = "https://www.lagou.com/jobs/positionAjax.json"

# 默认 CDP 端口（与 BOSS/Monster 共用，同一时刻只能一个爬虫用）
DEFAULT_CDP_PORT = 9222


# 在页面内执行 fetch 调用 API 的 JS 表达式
# 使用 application/x-www-form-urlencoded 格式（拉勾 API 要求）
FETCH_API_JS = """
async (params) => {
    const apiUrl = params.apiUrl;
    const body = params.body;
    const r = await fetch(apiUrl, {
        method: 'POST',
        credentials: 'include',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'X-Anit-Forge-Token': 'null',
            'X-Anit-Forge-Code': '0',
            'Accept': 'application/json, text/plain, */*',
        },
        body: body,
    });
    const text = await r.text();
    return JSON.stringify({status: r.status, body: text});
}
"""


async def crawl(keyword: str, city: str, max_pages: int = 3, cdp_port: int = DEFAULT_CDP_PORT) -> int:
    """通过 CDP 连接已启动的 Chrome/Edge，调用拉勾内部 API 采集岗位。

    前置条件：
    - 用户已运行 setup_boss_chrome.py 启动带 CDP 的浏览器
    - 用户已在浏览器中完成 lagou.com 的滑动验证 + 登录

    Args:
        keyword: 搜索关键词
        city: 城市（如 "北京"）
        max_pages: 最大页数
        cdp_port: CDP 端口

    Returns:
        采集到的岗位数
    """
    from playwright.async_api import async_playwright

    items = []

    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
        except Exception as e:
            log(f"❌ CDP 连接失败（端口 {cdp_port}）: {e}")
            log(f"   请先运行 setup_boss_chrome.py 启动带 CDP 的 Chrome/Edge")
            return 0

        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await context.new_page()

        # 先导航到拉勾搜索页，获取 Cookie + X-Anit-Token
        # 拉勾必须先访问搜索页，否则 API 会返回 403
        search_page_url = f"https://www.lagou.com/jobs/list_{quote(keyword)}?city={quote(city)}"
        log(f"导航到搜索页: {search_page_url}")
        try:
            await page.goto(search_page_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)
        except Exception as e:
            log(f"❌ 导航失败: {e}")
            await page.close()
            return 0

        # 检查是否被 WAF 拦截（标题为"滑动验证页面"）
        try:
            title = await page.title()
            log(f"页面标题: {title}")
            if "滑动验证" in title or "验证" in title:
                log(f"❌ 被 WAF 拦截，请在浏览器中手动完成滑动验证 + 登录后重试")
                log(f"   浏览器中访问: {search_page_url}")
                await page.close()
                return 0
            if "登录" in title:
                log(f"❌ 需要登录，请在浏览器中登录 lagou.com 后重试")
                await page.close()
                return 0
        except Exception:
            pass

        # 调用 positionAjax.json API 翻页采集
        try:
            current_page = 1
            while current_page <= max_pages:
                # 构造 API URL
                api_params = {
                    "px": "default",
                    "city": city,
                    "needAddtionalResult": "false",
                }
                api_url = f"{LAGOU_API_URL}?{urlencode(api_params)}"

                # POST body
                body = urlencode({
                    "first": "true" if current_page == 1 else "false",
                    "pn": current_page,
                    "kd": keyword,
                })

                log(f"[kw={keyword} city={city} page={current_page}] 调用 API...")

                # 在页面上下文中调用 fetch（Cookie 自动带上）
                raw = None
                last_err = None
                for attempt in range(3):
                    try:
                        raw = await page.evaluate(FETCH_API_JS, {"apiUrl": api_url, "body": body})
                        break
                    except Exception as e:
                        last_err = e
                        log(f"  fetch 失败 (attempt={attempt+1}/3): {e}")
                        if attempt < 2:
                            await page.wait_for_timeout(2000)
                            # 重新导航到搜索页恢复执行上下文
                            try:
                                await page.goto(search_page_url, wait_until="domcontentloaded", timeout=20000)
                                await page.wait_for_timeout(2000)
                            except Exception:
                                pass

                if raw is None:
                    log(f"❌ fetch 3 次重试均失败，跳过 page={current_page}: {last_err}")
                    break

                try:
                    fetch_result = json.loads(raw)
                except json.JSONDecodeError as e:
                    log(f"❌ fetch 结果 JSON 解析失败: {e}")
                    break

                if fetch_result.get("status") != 200:
                    log(f"❌ API HTTP {fetch_result.get('status')}")
                    # 检查是否被 WAF 拦截
                    body_text = fetch_result.get("body", "")[:500]
                    if "滑动验证" in body_text or "captcha" in body_text.lower():
                        log(f"   被 WAF 拦截，请在浏览器中完成验证后重试")
                    break

                body_text = fetch_result.get("body", "")
                try:
                    data = json.loads(body_text)
                except json.JSONDecodeError as e:
                    log(f"❌ API body JSON 解析失败: {e}")
                    log(f"   body 前 200 字符: {body_text[:200]}")
                    break

                # 检查 API 返回码
                if not data.get("success", False):
                    msg = data.get("msg", "")
                    log(f"❌ API 返回失败: success=false, msg={msg}")
                    if "频繁" in msg or "限制" in msg:
                        log(f"   触发限频，结束采集")
                        break
                    break

                # 解析岗位列表
                result = data.get("content", {}).get("positionResult", {})
                jobs = result.get("result", []) or []
                total_count = result.get("totalCount", 0)
                log(f"  [page={current_page}] 获取 {len(jobs)} 条岗位 (总计 {total_count})")

                if not jobs:
                    break

                for j in jobs:
                    position_id = j.get("positionId", "")
                    if not position_id:
                        continue

                    source_url = f"https://www.lagou.com/jobs/{position_id}.html"

                    # 技能标签
                    skill_tags = []
                    position_labels = j.get("positionLables", []) or []
                    skill_tags.extend([str(t) for t in position_labels])
                    company_labels = j.get("companyLabelList", []) or []
                    skill_tags.extend([str(t) for t in company_labels])

                    # 薪资
                    salary = j.get("salary", "")

                    # 经验/学历
                    work_year = j.get("workYear", "")
                    education = j.get("education", "")
                    job_nature = j.get("jobNature", "")

                    # 公司
                    company = j.get("companyShortName", "") or j.get("companyFullName", "")
                    company_size = j.get("companySize", "")
                    finance_stage = j.get("financeStage", "")
                    industry = j.get("industryField", "")

                    # 地点
                    city_name = j.get("city", "")
                    district = j.get("district", "")
                    location = "·".join(p for p in [city_name, district] if p)

                    # 职位优势
                    advantage = j.get("positionAdvantage", "")

                    items.append({
                        "source_id": str(position_id),
                        "source_url": source_url,
                        "title": j.get("positionName", ""),
                        "company": company,
                        "location": location,
                        "salary": salary,
                        "experience": work_year,
                        "education": education,
                        "job_type": job_nature,
                        "tags": skill_tags,
                        "description": advantage,  # 职位优势（简短描述）
                        "requirements": "",
                        "company_size": company_size,
                        "finance_stage": finance_stage,
                        "industry": industry,
                        "raw_text": json.dumps(j, ensure_ascii=False),
                    })

                if not jobs or current_page >= max_pages:
                    break

                # 翻页间隔 5-10 秒（拉勾限速 10 req/min）
                delay = random.uniform(5, 10)
                log(f"  翻页等待 {delay:.1f}s...")
                await asyncio.sleep(delay)
                current_page += 1

        finally:
            await page.close()

    # 输出 JSONL
    count = 0
    for item in items:
        print(json.dumps(item, ensure_ascii=False), flush=True)
        count += 1

    log(f"✅ 采集完成: kw={keyword} city={city} count={count}")
    return count


def main():
    parser = argparse.ArgumentParser(description="拉勾网 CDP 采集脚本（内部 API + 登录态复用）")
    parser.add_argument("--keyword", required=True, help="搜索关键词")
    parser.add_argument("--city", default="北京", help="城市（如 北京）")
    parser.add_argument("--max-pages", type=int, default=3, help="最大页数")
    parser.add_argument("--cdp-port", type=int, default=int(os.environ.get("BOSS_CDP_PORT", DEFAULT_CDP_PORT)),
                        help=f"CDP 端口（默认 {DEFAULT_CDP_PORT}，复用 BOSS 的隔离 Chrome）")
    args = parser.parse_args()

    log(f"CDP 端口: {args.cdp_port}")
    count = asyncio.run(crawl(args.keyword, args.city, args.max_pages, args.cdp_port))
    sys.exit(0 if count > 0 else 1)


if __name__ == "__main__":
    main()
