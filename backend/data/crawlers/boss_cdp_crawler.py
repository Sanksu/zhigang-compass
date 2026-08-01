"""BOSS 直聘 CDP 采集脚本（独立运行，避免事件循环冲突）。

被 BossSpider 通过 subprocess 调用，输出 JSONL 到 stdout。
也可以独立运行：python -m crawlers.boss_cdp_crawler --keyword Python --city 101010100

技术方案：用 Playwright CDP 连接已启动的 Chrome/Edge，复用真实登录态。
"""

import argparse
import asyncio
import hashlib
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode


# BOSS 内部搜索 API
BOSS_API_URL = "https://www.zhipin.com/wapi/zpgeek/search/joblist.json"

# 在页面内执行 fetch 调用 API 的 JS 表达式
FETCH_API_JS = """
async (apiUrl) => {
    const r = await fetch(apiUrl, {credentials: 'include'});
    const t = await r.text();
    return JSON.stringify({status: r.status, body: t});
}
"""


def log(msg):
    """输出日志到 stderr（不干扰 stdout 的 JSONL）。"""
    print(msg, file=sys.stderr, flush=True)


async def crawl(cdp_port: int, keyword: str, city_code: str, max_pages: int = 5) -> list:
    """通过 CDP 连接已启动的 Chrome，采集岗位数据。"""
    from playwright.async_api import async_playwright

    items = []

    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
        except Exception as e:
            log(f"CDP 连接失败: {e}")
            return items

        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await context.new_page()

        # 导航到具体搜索页（而非首页），避免首页重定向导致执行上下文被销毁
        # wait_until="networkidle" 等待网络空闲，确保页面完全加载
        search_page_url = f"https://www.zhipin.com/web/geek/job?query={keyword}&city={city_code}"
        try:
            await page.goto(search_page_url, wait_until="networkidle", timeout=30000)
            # 额外等待 DOM 稳定，避免 SPA 框架的重定向
            await page.wait_for_load_state("domcontentloaded")
        except Exception as e:
            log(f"导航到搜索页失败: {e}")
            # 降级：仅导航到首页，再尝试 evaluate
            try:
                await page.goto("https://www.zhipin.com/", wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(2000)
            except Exception as e2:
                log(f"降级导航也失败: {e2}")
                await page.close()
                return items

        try:
            current_page = 1
            while current_page <= max_pages:
                api_params = {
                    "scene": "1",
                    "query": keyword,
                    "city": city_code,
                    "experience": "",
                    "payType": "",
                    "partTime": "",
                    "degree": "",
                    "industry": "",
                    "scale": "",
                    "position": "",
                    "jobType": "",
                    "salary": "",
                    "multiBusinessDistrict": "",
                    "multiSubway": "",
                    "page": current_page,
                }
                api_url = f"{BOSS_API_URL}?{urlencode(api_params)}"

                # evaluate 失败时重试 2 次（页面可能因 SPA 路由切换导致上下文短暂销毁）
                raw = None
                last_err = None
                for attempt in range(3):
                    try:
                        raw = await page.evaluate(FETCH_API_JS, api_url)
                        break
                    except Exception as e:
                        last_err = e
                        log(f"fetch 调用失败 (page={current_page}, attempt={attempt+1}/3): {e}")
                        if attempt < 2:
                            await page.wait_for_timeout(2000)
                            # 重新导航到搜索页，恢复执行上下文
                            try:
                                await page.goto(search_page_url, wait_until="networkidle", timeout=20000)
                            except Exception:
                                pass
                if raw is None:
                    log(f"fetch 3 次重试均失败，跳过 page={current_page}: {last_err}")
                    break

                if not raw:
                    log(f"[page={current_page}] API 返回空")
                    break

                try:
                    fetch_result = json.loads(raw)
                except json.JSONDecodeError as e:
                    log(f"fetch 结果 JSON 解析失败: {e}")
                    break

                if fetch_result.get("status") != 200:
                    log(f"API HTTP {fetch_result.get('status')}")
                    break

                body = fetch_result.get("body", "")
                try:
                    data = json.loads(body)
                except json.JSONDecodeError as e:
                    log(f"API body JSON 解析失败: {e}")
                    break

                api_code = data.get("code")
                if api_code not in (0, None):
                    log(f"BOSS API 错误: code={api_code}, message={data.get('message', '')}")
                    break

                jobs = (data.get("zpData") or {}).get("jobList") or []
                log(f"[kw={keyword} city={city_code} page={current_page}] 获取 {len(jobs)} 条岗位")

                for j in jobs:
                    encrypt_job_id = j.get("encryptJobId", "")
                    if not encrypt_job_id:
                        continue

                    source_url = f"https://www.zhipin.com/job_detail/{encrypt_job_id}.html"
                    tech_tags = j.get("skills", []) or []
                    job_labels = j.get("jobLabels", []) or []
                    tags = list(tech_tags) + list(job_labels)

                    location_parts = [
                        j.get("cityName", ""),
                        j.get("areaDistrict", ""),
                        j.get("businessDistrict", ""),
                    ]
                    location = "·".join(p for p in location_parts if p)

                    # _fingerprint: source + source_id 的 SHA256，对齐 _BaseItem 定义
                    fp_input = f"boss:{encrypt_job_id}".encode("utf-8")
                    fingerprint = hashlib.sha256(fp_input).hexdigest()

                    # Boss API 列表页无 post_date 字段，发布日期需详情页（反爬无法获取）
                    items.append({
                        "source": "boss",
                        "source_id": encrypt_job_id,
                        "source_url": source_url,
                        "crawled_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                        "raw_text": json.dumps(j, ensure_ascii=False),
                        "is_desensitized": False,
                        "_fingerprint": fingerprint,
                        "title": j.get("jobName", ""),
                        "company": j.get("brandName", ""),
                        "location": location,
                        "salary": j.get("salaryDesc", ""),
                        "experience": j.get("jobExperience", ""),
                        "education": j.get("jobDegree", ""),
                        "tags": tags,
                        "description": "",
                        "requirements": "",
                        "post_date": "",
                    })

                if not jobs or current_page >= max_pages:
                    break

                # 翻页间隔 12-22 秒
                delay = random.uniform(12, 22)
                log(f"翻页等待 {delay:.1f}s...")
                await asyncio.sleep(delay)
                current_page += 1

        finally:
            # CDP 模式下不关闭 browser（避免关闭用户的浏览器），与其他 CDP 脚本一致
            await page.close()

    return items


def main():
    parser = argparse.ArgumentParser(description="BOSS 直聘 CDP 采集脚本")
    parser.add_argument("--keyword", required=True, help="搜索关键词")
    parser.add_argument("--city-code", required=True, help="城市代码（如 101010100）")
    parser.add_argument("--cdp-port", type=int, default=9222, help="CDP 端口")
    parser.add_argument("--max-pages", type=int, default=5, help="最大页数")
    args = parser.parse_args()

    items = asyncio.run(crawl(args.cdp_port, args.keyword, args.city_code, args.max_pages))

    # 输出 JSONL 到 stdout
    for item in items:
        print(json.dumps(item, ensure_ascii=False), flush=True)

    log(f"采集完成，共 {len(items)} 条")


if __name__ == "__main__":
    main()
