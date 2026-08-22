"""中国大学MOOC (icourse163) 独立采集脚本。

被 Icourse163Spider 通过 subprocess 调用，输出 JSONL 到 stdout。
也可以独立运行：python -m crawlers.icourse163_crawler --keyword Python --max-pages 3

策略：
- 用 Playwright 启动 headless Chromium，先导航到 search.htm 建立会话（cookie + csrfKey）
- 在页面上下文内执行 fetch 调用内部 RPC API
  `https://www.icourse163.org/web/j/mocSearchBean.searchCourse.rpc`
  绕过 csrfKey 校验（无需手工提取，fetch 自动携带 cookie + 同源凭据）
- 解析 JSON 响应中的 mocCourseKyCardBaseInfoDto / mocCourseCard 字段
- 仅采集 type=301（在线课程），跳过 type=308（教材）

合规：
- 仅采集公开课程元数据
- 每周全量同步，请求间隔 8-15s
- 不绕过登录态（icourse163 搜索页本身是公开的）
"""

import argparse
import asyncio
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.core.logging import setup_logging

logger = setup_logging("icourse163_crawler", stream=sys.stderr)


def _safe_int(value) -> int:
    """安全解析为 int（H3 修复）：API 可能返回 "3,600" / "1.2万" / "约8000人" 等。

    数值类直接 int；字符串剥离千分位/单位后缀/中文单位，无法解析返回 0，
    避免 int() 抛 ValueError 导致整批课程丢失（脚本异常会炸子进程）。
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str):
        return 0
    s = value.strip().replace(",", "").replace("，", "")
    # 中文单位：万
    if s.endswith("万"):
        try:
            return int(float(s[:-1]) * 10000)
        except ValueError:
            return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def _safe_float(value) -> float:
    """安全解析为 float：非数值返回 0.0（H3 修复，同 _safe_int 语义）。"""
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return 0.0
    try:
        return float(value.strip())
    except ValueError:
        return 0.0


# icourse163 搜索课程 RPC API
ICOURSE163_SEARCH_RPC = (
    "https://www.icourse163.org/web/j/mocSearchBean.searchCourse.rpc"
)

# 培训/应试类课程标题黑名单：关键词模糊匹配命中的课程（专升本辅导/期末冲刺等）
# 与岗位技能学习路径无关且质量差，直接跳过入库（2026-08-03 真实库含 29 条此类脏数据）
TITLE_BLACKLIST = ("专升本", "期末")

# 在页面内执行 fetch 调用 API 的 JS 表达式
# 注意：csrfKey 在 URL 上由页面 JS 动态生成，最稳的做法是直接在页面上下文
# 调 fetch 让浏览器自己处理同源和 cookie，然后再拼上同源的 csrfKey。
# 但 csrfKey 是会话内随机生成的 32 位 hex，每次页面加载都不一样。
# 这里用一个更稳妥的方案：拦截页面首次发起的 csrfKey，复用它。
FETCH_API_JS = """
async (params) => {
    // 从页面已有的 RPC URL 中提取 csrfKey（页面初始化时已经发过若干 RPC）
    const scripts = performance.getEntriesByType('resource')
        .map(e => e.name)
        .filter(u => u.includes('csrfKey='));
    let csrfKey = '';
    if (scripts.length > 0) {
        const m = scripts[scripts.length - 1].match(/csrfKey=([a-f0-9]+)/);
        if (m) csrfKey = m[1];
    }
    // 兜底：从 cookie 提取（NTESSTUDYSI）
    if (!csrfKey) {
        const cm = document.cookie.match(/NTESSTUDYSI=([a-f0-9]+)/);
        if (cm) csrfKey = cm[1];
    }
    if (!csrfKey) {
        return JSON.stringify({status: 0, body: '', error: 'no csrfKey found'});
    }
    const url = params.url + '?csrfKey=' + csrfKey;
    const r = await fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
            'Accept': 'application/json, text/plain, */*',
        },
        body: params.body,
        credentials: 'include',
    });
    const t = await r.text();
    return JSON.stringify({status: r.status, body: t});
}
"""


def build_query(keyword: str, page_index: int, page_size: int = 20) -> str:
    """构造 mocCourseQueryVo 参数。"""
    query = {
        "keyword": keyword,
        "pageIndex": page_index,
        "highlight": True,
        "orderBy": 0,
        "stats": 30,
        "pageSize": page_size,
    }
    return urlencode({"mocCourseQueryVo": json.dumps(query, ensure_ascii=False)})


def parse_course_list(api_data: dict, keyword: str) -> list:
    """解析 searchCourse RPC 响应，返回 CourseItem dict 列表。"""
    items = []
    result = api_data.get("result") or {}
    lst = result.get("list") or []

    for entry in lst:
        # type=301 在线课程、type=306 专业/培训课程（均含 courseId/课程名，可入库）；
        # type=308 是教材，跳过
        if entry.get("type") not in (301, 306):
            continue

        card = entry.get("mocCourseKyCardBaseInfoDto") or {}
        course_card = (entry.get("mocCourseCard") or {}).get("mocCourseCardDto") or {}
        term_panel = course_card.get("termPanel") or {}
        school = course_card.get("schoolPanel") or {}

        course_id = str(card.get("courseId") or entry.get("courseId") or "")
        if not course_id:
            continue

        course_name = (
            card.get("courseName")
            or course_card.get("name")
            or entry.get("highlightName", "").replace("{##", "").replace("##}", "")
            or ""
        ).strip()

        # 去掉 highlight 标记 {##xxx##}
        if "{##" in course_name:
            course_name = course_name.replace("{##", "").replace("##}", "")

        if not course_name:
            continue

        # 培训/应试类课程（专升本/期末冲刺等）与技能学习路径无关，跳过
        if any(black in course_name for black in TITLE_BLACKLIST):
            logger.warning(f"  跳过培训/应试类课程: {course_name[:50]}")
            continue

        # 课程页 URL 必须带学校简称前缀（/course/{shortName}-{courseId}），
        # 纯数字路径 404 → commonError.htm 错误页（08-22 实测 891 门存量全坏）
        school_short = school.get("shortName") or ""
        if school_short:
            source_url = f"https://www.icourse163.org/course/{school_short}-{course_id}"
        else:
            logger.warning(f"  缺 schoolPanel.shortName，URL 回退纯数字（不可访问）: {course_name[:50]}")
            source_url = f"https://www.icourse163.org/course/{course_id}"
        source_id = course_id

        # 讲师
        lectors = term_panel.get("lectorPanels") or []
        instructor = (
            lectors[0].get("realName") or lectors[0].get("nickName") or ""
            if lectors
            else card.get("teacherName", "")
        )

        # 院校
        institution = school.get("name") or entry.get("highlightUniversity") or ""

        # 描述
        description = term_panel.get("jsonContent") or ""

        # 注册人数
        enrollment = card.get("enrollNum") or term_panel.get("enrollCount") or 0

        # 时长（duration 字段：kaoyan/term 等，转为可读字符串）
        duration = str(term_panel.get("duration") or "")

        # 开课时间（毫秒时间戳 → ISO）
        start_ts = term_panel.get("startTime")
        end_ts = term_panel.get("endTime")
        start_date = ""
        if isinstance(start_ts, (int, float)) and start_ts > 0:
            start_date = datetime.fromtimestamp(start_ts / 1000, tz=timezone.utc).date().isoformat()

        # 标签
        tags_raw = card.get("tags") or []
        tags = [t.get("name", "") for t in tags_raw if t.get("name")]

        end_date = ""
        if isinstance(end_ts, (int, float)) and end_ts > 0:
            end_date = datetime.fromtimestamp(end_ts / 1000, tz=timezone.utc).date().isoformat()

        items.append({
            "source_id": source_id,
            "source_url": source_url,
            "title": course_name,
            "instructor": instructor,
            "institution": institution,
            "platform": "icourse163",
            "category": keyword,
            "description": description,
            "rating": 0.0,
            "enrollment": _safe_int(enrollment),
            "duration": duration,
            "start_date": start_date,
            "skills": tags,
            "end_date": end_date,
            "raw_text": json.dumps(entry, ensure_ascii=False),
        })

    return items


async def crawl(keyword: str, max_pages: int = 3, page_size: int = 20) -> list:
    """采集单个关键词的课程数据。"""
    from playwright.async_api import async_playwright

    items: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
        )
        page = await context.new_page()

        # 1. 导航到搜索页（建立会话，页面 JS 会自动调用一次 searchCourse 拿到 csrfKey）
        search_url = f"https://www.icourse163.org/search.htm?search={keyword}"
        logger.info(f"导航到 {search_url}")
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            logger.error(f"导航失败: {e}")
            await browser.close()
            return items

        # 等页面发起首次 RPC（拿到 csrfKey 放进 performance entries）
        try:
            await page.wait_for_function(
                """() => performance.getEntriesByType('resource')
                       .some(e => e.name.includes('csrfKey='))""",
                timeout=20000,
            )
        except Exception as e:
            logger.warning(f"等待 csrfKey 出现超时: {e}")

        # 2. 翻页调用 API
        for page_index in range(1, max_pages + 1):
            body = build_query(keyword, page_index, page_size)
            params = {"url": ICOURSE163_SEARCH_RPC, "body": body}

            try:
                raw = await page.evaluate(FETCH_API_JS, params)
            except Exception as e:
                logger.error(f"page.evaluate 失败 (page={page_index}): {e}")
                break

            if not raw:
                logger.warning(f"[page={page_index}] 返回空")
                break

            try:
                fetch_result = json.loads(raw)
            except json.JSONDecodeError as e:
                logger.error(f"fetch 结果 JSON 解析失败: {e}")
                break

            if fetch_result.get("error"):
                logger.error(f"[page={page_index}] {fetch_result['error']}")
                break

            if fetch_result.get("status") != 200:
                logger.error(f"[page={page_index}] HTTP {fetch_result.get('status')}")
                break

            body_text = fetch_result.get("body", "")
            try:
                api_data = json.loads(body_text)
            except json.JSONDecodeError as e:
                logger.error(f"[page={page_index}] body JSON 解析失败: {e}")
                break

            code = api_data.get("code")
            if code not in (0, None):
                logger.error(f"[page={page_index}] API code={code} message={api_data.get('message', '')}")
                break

            result = api_data.get("result") or {}
            query_info = result.get("query") or {}
            total_count = query_info.get("totleCount", 0)
            total_pages = query_info.get("totlePageCount", 0)
            lst = result.get("list") or []

            page_items = parse_course_list(api_data, keyword)
            logger.info(
                f"[kw={keyword} page={page_index}] 获取 {len(page_items)} 条课程"
                f"（API 返回 {len(lst)} 条，总 {total_count} 条 / {total_pages} 页）"
            )
            items.extend(page_items)

            if not lst or page_index >= max_pages or page_index >= total_pages:
                break

            # 翻页间隔 6-12 秒
            delay = random.uniform(6, 12)
            logger.info(f"翻页等待 {delay:.1f}s...")
            await asyncio.sleep(delay)

        await browser.close()

    return items


def main() -> int:
    parser = argparse.ArgumentParser(description="中国大学MOOC (icourse163) 采集脚本")
    parser.add_argument("--keyword", required=True, help="搜索关键词")
    parser.add_argument("--max-pages", type=int, default=3, help="最大页数（默认 3）")
    parser.add_argument("--page-size", type=int, default=20, help="每页数量（默认 20）")
    args = parser.parse_args()

    items = asyncio.run(
        crawl(args.keyword, max_pages=args.max_pages, page_size=args.page_size)
    )

    for item in items:
        print(json.dumps(item, ensure_ascii=False), flush=True)

    logger.info(f"采集完成，共 {len(items)} 条")
    # 0 条产出视为失败，返回非 0 退出码供上游 spider/调度器识别
    return 0 if items else 1


if __name__ == "__main__":
    sys.exit(main())
