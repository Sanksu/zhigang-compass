"""脉脉 CDP 采集脚本（独立运行，避免与 Scrapy Twisted 事件循环冲突）。

被 spiders/maimai.py 通过 subprocess 调用，输出 JSONL 到 stdout。
状态/错误日志输出到 stderr。

核心策略（2026-07-29 重构）：
- 旧方案：maimai.cn/job/search 是专栏页，无岗位数据；maimai.cn 上无公开职位搜索页
- 新发现：脉脉职位实际托管在飞书招聘系统 maimai.jobs.feishu.cn
- 飞书招聘页 SSR 渲染 41 个岗位卡片，无需登录态即可采集
- 从 DOM 提取 a[href*="position"] 卡片（飞书招聘标准结构）

合规措施（S2+S3，project_memory 强制约束）：
- 注明用于竞赛演示不商用（X-Collection-Purpose 头）
- 数据脱敏（CleaningPipeline 自动 PII 清洗：手机号/邮箱/身份证）
- 限频 ≤100 req/h（settings.RATE_LIMIT.maimai = 5 req/min）
- 夜间运行 22:00-08:00（start_requests 时间守卫强制）

DOM 结构（2026-07-29 实测）：
- 岗位卡片: a[href*="/index/position/{id}/detail"]
- 卡片文本: 标题 + 城市 + 全职/兼职 + 类别 + 岗位描述
- 详情页: https://maimai.jobs.feishu.cn/index/position/{id}/detail

注意：脉脉.jobs.feishu.cn 是脉脉公司自己的招聘页（招聘脉脉员工），
非脉脉平台全量职位。作为 C 级实验性源的样本数据足够。
"""

import argparse
import asyncio
import json
import os
import re
import sys
from urllib.parse import urljoin


def log(msg: str):
    """日志输出到 stderr，不污染 stdout 的 JSONL。"""
    print(msg, file=sys.stderr, flush=True)


# 默认 CDP 端口（与 BOSS/Monster 共用，同一时刻只能一个爬虫用）
DEFAULT_CDP_PORT = 9222
# 默认 CDP 端点（可由环境变量 BOSS_CDP_URL 覆盖，支持局域网内容器浏览器）
DEFAULT_CDP_URL = os.environ.get("BOSS_CDP_URL", f"http://127.0.0.1:{DEFAULT_CDP_PORT}")

# 脉脉飞书招聘页 URL
MAIMAI_JOBS_URL = "https://maimai.jobs.feishu.cn/index"


# 从 DOM 提取岗位卡片的 JS 表达式
EXTRACT_JOBS_JS = """
() => {
    const result = [];
    // 飞书招聘页岗位卡片: a[href*="/index/position/{id}/detail"]
    const cards = document.querySelectorAll('a[href*="/position/"]');
    for (const card of cards) {
        const href = card.href || '';
        // 提取 position id
        const idMatch = href.match(/\\/position\\/([\\d]+)/);
        const positionId = idMatch ? idMatch[1] : '';
        if (!positionId) continue;

        // 卡片文本按换行拆分
        const text = (card.innerText || '').trim();
        const lines = text.split('\\n').map(s => s.trim()).filter(Boolean);
        if (lines.length === 0) continue;

        // 第一行是标题
        const title = lines[0] || '';
        // 第二行: 城市 + 全职/兼职 + 类别，如 "北京全职互联网 / 电子 / 网游 - 运营"
        const metaLine = lines[1] || '';
        // 解析城市（中文城市名通常在开头）
        let city = '';
        const cityMatch = metaLine.match(/^(北京|上海|广州|深圳|成都|大连|杭州|南京|武汉|西安|苏州|天津|重庆|长沙|青岛|郑州|宁波|厦门|福州|合肥|济南|沈阳|哈尔滨|长春|昆明|南宁|贵阳|拉萨|乌鲁木齐|银川|太原|石家庄|呼和浩特|海口|兰州|西宁|南昌)/);
        if (cityMatch) city = cityMatch[1];

        // 解析工作类型（全职/兼职/实习）
        let jobType = '';
        if (metaLine.includes('全职')) jobType = '全职';
        else if (metaLine.includes('兼职')) jobType = '兼职';
        else if (metaLine.includes('实习')) jobType = '实习';

        // 解析类别（metaLine 中城市和工作类型之后的部分）
        let category = '';
        if (city) {
            const afterCity = metaLine.slice(city.length);
            const typeIdx = afterCity.search(/全职|兼职|实习/);
            if (typeIdx >= 0) {
                // 用 typeIdx 定位类型词后截取（旧的 indexOf||短路在"全职"缺失时会误截首字符）
                category = afterCity.slice(typeIdx + 2).trim();
            }
        }

        // 描述：第二行之后的所有内容
        const description = lines.slice(2).join('\\n').trim();

        result.push({
            source_id: positionId,
            source_url: href,
            title: title,
            company: '脉脉',  // 飞书招聘页是脉脉公司自己的招聘
            location: city,
            salary: '',  // 飞书招聘页列表通常不显示薪资
            job_type: jobType,
            category: category,
            description: description,
        });
    }
    return result;
}
"""


async def crawl(keyword: str, cdp_url: str = DEFAULT_CDP_URL) -> int:
    """通过 CDP 连接已启动的 Chrome/Edge，从脉脉飞书招聘页提取岗位。

    前置条件：
    - 用户已运行 setup_boss_chrome.py 启动带 CDP 的浏览器
    - 飞书招聘页无需登录态，直连即可访问

    Args:
        keyword: 搜索关键词（脉脉飞书页无搜索功能，仅作日志记录）
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

        # 隔离：新建独立 context 并复制主 context 的 cookies，
        # 爬虫导航只发生在隔离 context 内，不触碰用户正在浏览的页面
        context = await browser.new_context()
        if browser.contexts:
            try:
                _cookies = await browser.contexts[0].cookies()
                if _cookies:
                    await context.add_cookies(_cookies)
            except Exception as e:
                log(f"⚠️ 复制 cookies 到隔离 context 失败: {e}")
        page = await context.new_page()

        log(f"导航到: {MAIMAI_JOBS_URL}")
        try:
            await page.goto(MAIMAI_JOBS_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            log(f"❌ 导航失败: {e}")
            return 0

        # 等待岗位卡片渲染
        try:
            await page.wait_for_selector('a[href*="/position/"]', timeout=15000)
        except Exception as e:
            log(f"⚠️ 等待岗位卡片超时: {e}")
            try:
                title = await page.title()
                log(f"  页面标题: {title}")
            except Exception:
                pass
            await page.close()
            return 0

        # 额外等待 SSR 数据完全渲染
        await page.wait_for_timeout(2000)

        # 持续滚动到底部，触发所有岗位卡片渲染（飞书招聘页用懒加载）
        # 最多滚动 10 次，或直到岗位数不再增长
        prev_count = 0
        for scroll_idx in range(10):
            try:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1500)
                # 检查当前岗位卡片数
                current_count = await page.evaluate(
                    "document.querySelectorAll('a[href*=\"/position/\"]').length"
                )
                if current_count == prev_count:
                    # 岗位数不再增长，停止滚动
                    break
                prev_count = current_count
                log(f"  滚动 {scroll_idx+1}: 当前 {current_count} 个岗位卡片")
            except Exception:
                break

        # 从 DOM 提取岗位
        try:
            jobs = await page.evaluate(EXTRACT_JOBS_JS)
        except Exception as e:
            log(f"❌ DOM 提取失败: {e}")
            await page.close()
            return 0

        log(f"提取到 {len(jobs)} 条岗位")

        all_jobs_data.extend(jobs)
        await page.close()

    # 输出 JSONL
    count = 0
    for job in all_jobs_data:
        item = _map_job_to_item(job, keyword)
        if item:
            print(json.dumps(item, ensure_ascii=False), flush=True)
            count += 1

    log(f"✅ 采集完成: kw={keyword} count={count}")
    return count


def _map_job_to_item(job: dict, keyword: str) -> dict | None:
    """将 DOM 提取的岗位数据映射为统一 Item 格式。"""
    try:
        job_id = str(job.get("source_id", ""))
        title = str(job.get("title", ""))
        if not job_id or not title:
            return None

        return {
            "id": f"maimai-{job_id}",
            "title": title,
            "company": str(job.get("company", "脉脉")),
            "location": str(job.get("location", "")),
            "salary": str(job.get("salary", "")),
            "description": str(job.get("description", "")),
            "url": str(job.get("source_url", "")),
            "is_remote": False,
            "skills": [],
            "date_posted": "",
            "job_type": str(job.get("job_type", "")),
            "category": str(job.get("category", "")),
            "company_industry": "互联网",
            "experience_range": "",
            "raw": job,
        }
    except Exception as e:
        log(f"⚠️ 字段映射失败: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="脉脉 CDP 采集脚本（飞书招聘页 DOM 提取）")
    parser.add_argument("--keyword", default="", help="搜索关键词（仅作日志，飞书招聘页无搜索功能）")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL,
                        help="CDP 调试端点（默认 http://127.0.0.1:9222，支持局域网内容器浏览器）")
    args = parser.parse_args()

    log(f"CDP 端点: {args.cdp_url}")
    count = asyncio.run(crawl(args.keyword, args.cdp_url))
    sys.exit(0 if count > 0 else 1)


if __name__ == "__main__":
    main()
