"""BOSS 直聘采集脚本（独立运行，避免与 Scrapy Twisted 事件循环冲突）。

方案演进（2026-08-04）：
- 旧方案：CDP 连接浏览器 → 页面内 evaluate fetch 调 BOSS 内部 API。
  问题：zhipin 反爬检测 CDP 自动化，页面内 fetch 被拦截（Failed to fetch），
  导航 zhipin 甚至触发风控关闭整个浏览器（实测）。
- 新方案：CDP 仅读取浏览器登录态 cookies，采集走纯 HTTP（httpx）直接调 API。
  实测服务端对带登录 cookies 的正常 HTTP 请求不拦截（code=0 正常返回岗位）。
  不导航页面、不执行页面 JS，浏览器保持存活，登录态仅作为 cookies 来源。
- cookies 文件模式（2026-08-06）：登录态可先经 --export-cookies 导出成文件，
  容器等无 CDP 浏览器环境用 --cookies-file 直接读取，采集完全不需要浏览器。

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
from pathlib import Path
from urllib.parse import urlencode

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.core.logging import setup_logging

logger = setup_logging("boss_cdp_crawler", stream=sys.stderr)

# BOSS 内部搜索 API
BOSS_API_URL = "https://www.zhipin.com/wapi/zpgeek/search/joblist.json"

# BOSS jobList 时间字段候选（按优先级取第一个非空；字段名待 T1 实测后锁定）
_POST_DATE_FIELDS = ("lastModifyTime", "publishTime", "updateTime")

# 东八区
_CST = timezone(timedelta(hours=8))

# 与浏览器一致的请求头（复用登录态 + 真实指纹 UA）
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
    "Referer": "https://www.zhipin.com/",
    "Accept": "application/json, text/plain, */*",
}


def _coerce_ts(raw) -> str | None:
    """把时间字段值规整为 ISO 字符串。

    支持：秒/毫秒时间戳（int/float/数字字符串）→ 东八区 ISO；
    ISO 字符串原样返回；其余返回 None。
    """
    if isinstance(raw, str):
        s = raw.strip()
        if s.isdigit():
            raw = int(s)
        else:
            try:
                return datetime.fromisoformat(s).isoformat()
            except ValueError:
                return None
    if isinstance(raw, (int, float)):
        ts = int(raw)
        if ts > 10**12:  # 毫秒时间戳
            ts //= 1000
        try:
            return datetime.fromtimestamp(ts, tz=_CST).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _extract_post_date(job: dict) -> str:
    """从候选时间字段提取发布时间（ISO 字符串）。

    BOSS 时间字段名依赖 T1 实测，按候选枚举取第一个可解析值；
    全空返回空串（外部数据缺失是合法状态）。
    """
    for field in _POST_DATE_FIELDS:
        value = job.get(field)
        if value in (None, ""):
            continue
        parsed = _coerce_ts(value)
        if parsed:
            return parsed
    return ""


def _is_older_than_days(post_date: str, days: int) -> bool:
    """发布时间是否早于 days 天前。无法解析返回 False（不误截断）。"""
    try:
        dt = datetime.fromisoformat(post_date)
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_CST)
    cutoff = datetime.now(_CST) - timedelta(days=days)
    return dt < cutoff


def _load_cookies_from_file(path: str) -> httpx.Cookies | None:
    """从 JSON cookies 文件读取 zhipin cookies（--export-cookies 导出的同格式文件）。"""
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as e:
        logger.error(f"⚠️ cookies 文件读取失败（{path}）: {e}")
        return None

    jar = httpx.Cookies()
    count = 0
    for c in raw:
        jar.set(c.get("name", ""), c.get("value", ""),
                domain=c.get("domain", ""), path=c.get("path", "/"))
        count += 1
    if not count:
        logger.warning(f"⚠️ cookies 文件为空（{path}）")
        return None
    logger.info(f"✅ 已从文件读取 {count} 个 zhipin cookies（{path}）")
    return jar


async def _read_zhipin_cookies_raw(cdp_url: str) -> list[dict]:
    """经 CDP 读取浏览器 zhipin cookies 原始 dict（供导出序列化）。"""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(cdp_url)
        except Exception as e:
            logger.error(f"❌ CDP 连接失败（{cdp_url}）: {e}")
            return []
        try:
            if browser.contexts:
                all_cookies = await browser.contexts[0].cookies()
                zhipin = [c for c in all_cookies if "zhipin" in c.get("domain", "")]
                if zhipin:
                    logger.info(f"✅ 已读取 {len(zhipin)} 个 zhipin cookies（登录态有效）")
                    return zhipin
                logger.warning("⚠️ 主 context 无 zhipin cookies（未登录）")
        except Exception as e:
            logger.error(f"⚠️ 读取 cookies 失败: {e}")
    return []


async def read_zhipin_cookies(cdp_url: str, cookies_file: str | None = None) -> httpx.Cookies | None:
    """获取 zhipin 登录态 cookies：优先从文件读取，无文件时经 CDP 读取。

    Returns:
        httpx.Cookies；无 zhipin 登录 cookies 时返回 None。
    """
    if cookies_file:
        jar = _load_cookies_from_file(cookies_file)
        if jar is not None:
            return jar
        logger.warning(f"⚠️ cookies 文件不可用，回退 CDP 读取（{cdp_url}）")

    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(cdp_url)
        except Exception as e:
            logger.error(f"❌ CDP 连接失败（{cdp_url}）: {e}")
            return None
        try:
            if browser.contexts:
                all_cookies = await browser.contexts[0].cookies()
                zhipin = [c for c in all_cookies if "zhipin" in c.get("domain", "")]
                if zhipin:
                    jar = httpx.Cookies()
                    for c in zhipin:
                        jar.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))
                    logger.info(f"✅ 已读取 {len(zhipin)} 个 zhipin cookies（登录态有效）")
                    return jar
            logger.warning("⚠️ 主 context 无 zhipin cookies（未登录）")
        except Exception as e:
            logger.error(f"⚠️ 读取 cookies 失败: {e}")
    return None


async def export_cookies(cdp_url: str, out_path: str) -> int:
    """经 CDP 读取 zhipin cookies 并序列化到文件（供无浏览器环境复用登录态）。"""
    raw = await _read_zhipin_cookies_raw(cdp_url)
    if not raw:
        return 0
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    return len(raw)


async def crawl(cdp_url: str, keyword: str, city_code: str, max_pages: int = 5,
                cookies_file: str | None = None, since_days: int | None = None) -> list:
    """读取登录态 cookies，纯 HTTP 采集 BOSS 岗位。

    不导航页面、不执行页面 JS，避免触发 zhipin 风控（页面内 fetch 被拦、
    导航会关闭浏览器；纯 HTTP 带 cookies 请求正常返回岗位）。

    since_days: 只保留近 N 天发布的岗位；翻页遇到早于 N 天的岗位即停止
        （列表按发布时间倒序，方案 B 截断；字段名待 T1 实测后锁定）。
    """
    cookies = await read_zhipin_cookies(cdp_url, cookies_file)
    if cookies is None:
        logger.warning("⚠️ 未读取到 zhipin 登录态：请在弹出的 Chrome 中【手动】打开 zhipin.com 完成登录后重跑爬虫")
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
                logger.error(f"[page={page_num}] API 请求/解析失败: {e}")
                break

            code = data.get("code")
            if code != 0:
                logger.error(f"BOSS API 错误: code={code}, message={data.get('message', '')}")
                if code in (35, 36, 37):
                    logger.warning("⚠️ BOSS 风控/登录态失效：请在弹出的 Chrome 中重新完成登录后重跑爬虫")
                break

            jobs = (data.get("zpData") or {}).get("jobList") or []
            logger.info(f"[kw={keyword} city={city_code} page={page_num}] 获取 {len(jobs)} 条岗位")

            hit_old = False
            for j in jobs:
                encrypt_job_id = j.get("encryptJobId", "")
                if not encrypt_job_id:
                    continue
                # G-01 历史回爬：过滤早于 since_days 天的岗位，遇旧岗位即停翻页
                post_date = _extract_post_date(j)
                if since_days and post_date and _is_older_than_days(post_date, since_days):
                    hit_old = True
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
                    "post_date": post_date,
                })

            if not jobs or page_num >= max_pages:
                break
            if hit_old:
                logger.info(f"[kw={keyword} city={city_code}] 本页已出现 {since_days} 天前岗位，提前停止翻页")
                break
            # 翻页间隔（低频率，尊重平台限频）
            delay = random.uniform(12, 22)
            logger.info(f"翻页等待 {delay:.1f}s...")
            await asyncio.sleep(delay)

    return items


def main():
    parser = argparse.ArgumentParser(description="BOSS 直聘采集脚本（cookies 文件 / CDP 读 cookies + HTTP 采集）")
    parser.add_argument("--keyword", help="搜索关键词")
    parser.add_argument("--city-code", help="城市代码（如 101010100）")
    parser.add_argument("--cdp-url", default=os.environ.get("BOSS_CDP_URL", "http://127.0.0.1:9222"),
                        help="CDP 调试端点（默认 http://127.0.0.1:9222）")
    parser.add_argument("--max-pages", type=int, default=5, help="最大页数")
    parser.add_argument("--since-days", type=int, default=None,
                        help="只保留近 N 天发布的岗位（历史回爬 G-01；遇旧岗位即停翻页）")
    parser.add_argument("--cookies-file", default=os.environ.get("BOSS_COOKIES_FILE"),
                        help="JSON cookies 文件路径（采集模式优先从文件读登录态，无浏览器依赖）")
    parser.add_argument("--export-cookies", default=None,
                        help="导出模式：经 CDP 读 zhipin cookies 序列化到指定文件后退出")
    args = parser.parse_args()

    # 导出模式：--export-cookies 与采集参数互斥
    if args.export_cookies:
        n = asyncio.run(export_cookies(args.cdp_url, args.export_cookies))
        if n:
            logger.info(f"✅ 已导出 {n} 个 zhipin cookies → {args.export_cookies}")
            sys.exit(0)
        logger.error("❌ 未读取到 zhipin 登录态：请确认 CDP Chrome 已登录 zhipin.com 后重试")
        sys.exit(1)

    if not args.keyword or not args.city_code:
        parser.error("采集模式需要 --keyword 与 --city-code（或使用 --export-cookies 导出模式）")

    items = asyncio.run(crawl(args.cdp_url, args.keyword, args.city_code, args.max_pages,
                              args.cookies_file, args.since_days))

    # 输出 JSONL 到 stdout
    for item in items:
        print(json.dumps(item, ensure_ascii=False), flush=True)

    logger.info(f"✅ 采集完成: kw={args.keyword} city={args.city_code} count={len(items)}")
    # 非零退出码让 spider 端感知失败（与其他 CDP 采集脚本一致）
    sys.exit(0 if items else 1)


if __name__ == "__main__":
    main()
