"""BOSS 直聘采集脚本（独立运行，避免与 Scrapy Twisted 事件循环冲突）。

方案演进（2026-08-04）：
- 旧方案：CDP 连接浏览器 → 页面内 evaluate fetch 调 BOSS 内部 API。
  问题：zhipin 反爬检测 CDP 自动化，页面内 fetch 被拦截（Failed to fetch），
  导航 zhipin 甚至触发风控关闭整个浏览器（实测）。
- 新方案：CDP 仅读取浏览器登录态 cookies，采集走纯 HTTP（httpx）直接调 API。
  实测服务端对带登录 cookies 的正常 HTTP 请求不拦截（code=0 正常返回岗位）。
  不导航页面、不执行页面 JS，浏览器保持存活，登录态仅作为 cookies 来源。

被 spiders/boss.py 通过 subprocess 调用，输出 JSONL 到 stdout。
前置条件：已启动 CDP Chrome 并手动登录 zhipin.com（登录态持久到 profile）。
"""

import argparse
import asyncio
import hashlib
import json
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

# BOSS 内部搜索 API
BOSS_API_URL = "https://www.zhipin.com/wapi/zpgeek/search/joblist.json"

# 与浏览器一致的请求头（复用登录态 + 真实指纹 UA）
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    "Referer": "https://www.zhipin.com/",
    "Accept": "application/json, text/plain, */*",
}


def log(msg):
    """输出日志到 stderr（不干扰 stdout 的 JSONL）。"""
    print(msg, file=sys.stderr, flush=True)


async def read_zhipin_cookies(cdp_url: str) -> httpx.Cookies | None:
    """经 CDP 读取浏览器登录态 cookies（仅读取，不导航/不操作页面）。

    Returns:
        httpx.Cookies；无 zhipin 登录 cookies 时返回 None。
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(cdp_url)
        except Exception as e:
            log(f"❌ CDP 连接失败（{cdp_url}）: {e}")
            return None
        try:
            if browser.contexts:
                all_cookies = await browser.contexts[0].cookies()
                zhipin = [c for c in all_cookies if "zhipin" in c.get("domain", "")]
                if zhipin:
                    jar = httpx.Cookies()
                    for c in zhipin:
                        jar.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))
                    log(f"✅ 已读取 {len(zhipin)} 个 zhipin cookies（登录态有效）")
                    return jar
            log("⚠️ 主 context 无 zhipin cookies（未登录）")
        except Exception as e:
            log(f"⚠️ 读取 cookies 失败: {e}")
    return None


async def crawl(cdp_url: str, keyword: str, city_code: str, max_pages: int = 5) -> list:
    """读取登录态 cookies，纯 HTTP 采集 BOSS 岗位。

    不导航页面、不执行页面 JS，避免触发 zhipin 风控（页面内 fetch 被拦、
    导航会关闭浏览器；纯 HTTP 带 cookies 请求正常返回岗位）。
    """
    cookies = await read_zhipin_cookies(cdp_url)
    if cookies is None:
        log("⚠️ 未读取到 zhipin 登录态：请在弹出的 Chrome 中【手动】打开 zhipin.com 完成登录后重跑爬虫")
        return []

    items = []
    with httpx.Client(cookies=cookies, headers=_HEADERS, timeout=20, follow_redirects=True) as client:
        for page_num in range(1, max_pages + 1):
            params = {
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
                "page": page_num,
            }
            api_url = f"{BOSS_API_URL}?{urlencode(params)}"
            try:
                resp = client.get(api_url)
                data = resp.json()
            except Exception as e:
                log(f"[page={page_num}] API 请求/解析失败: {e}")
                break

            code = data.get("code")
            if code != 0:
                log(f"BOSS API 错误: code={code}, message={data.get('message', '')}")
                if code in (35, 36, 37):
                    log("⚠️ BOSS 风控/登录态失效：请在弹出的 Chrome 中重新完成登录后重跑爬虫")
                break

            jobs = (data.get("zpData") or {}).get("jobList") or []
            log(f"[kw={keyword} city={city_code} page={page_num}] 获取 {len(jobs)} 条岗位")

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
                fp_input = f"boss:{encrypt_job_id}".encode("utf-8")
                items.append({
                    "source": "boss",
                    "source_id": encrypt_job_id,
                    "source_url": source_url,
                    "crawled_at": datetime.now(timezone(timedelta(hours=8))).isoformat(),
                    "raw_text": json.dumps(j, ensure_ascii=False),
                    "is_desensitized": False,
                    "_fingerprint": hashlib.sha256(fp_input).hexdigest(),
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

            if not jobs or page_num >= max_pages:
                break
            # 翻页间隔（低频率，尊重平台限频）
            delay = random.uniform(12, 22)
            log(f"翻页等待 {delay:.1f}s...")
            await asyncio.sleep(delay)

    return items


def main():
    parser = argparse.ArgumentParser(description="BOSS 直聘采集脚本（CDP 读 cookies + HTTP 采集）")
    parser.add_argument("--keyword", required=True, help="搜索关键词")
    parser.add_argument("--city-code", required=True, help="城市代码（如 101010100）")
    parser.add_argument("--cdp-url", default=os.environ.get("BOSS_CDP_URL", "http://127.0.0.1:9222"),
                        help="CDP 调试端点（默认 http://127.0.0.1:9222，支持局域网内容器浏览器）")
    parser.add_argument("--max-pages", type=int, default=5, help="最大页数")
    args = parser.parse_args()

    items = asyncio.run(crawl(args.cdp_url, args.keyword, args.city_code, args.max_pages))

    # 输出 JSONL 到 stdout
    for item in items:
        print(json.dumps(item, ensure_ascii=False), flush=True)

    log(f"✅ 采集完成: kw={args.keyword} city={args.city_code} count={len(items)}")
    # 非零退出码让 spider 端感知失败（与其他 CDP 采集脚本一致）
    sys.exit(0 if items else 1)


if __name__ == "__main__":
    main()
