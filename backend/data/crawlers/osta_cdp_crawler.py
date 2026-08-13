"""技能人才评价网（OSTA）职业目录 CDP 采集器（2026-08-13）。

目标：www.osta.org.cn/career（SPA 职业目录页）的 `/client/career/query` 与
`/client/get/tree` API——curl 直取被 302 路由保护（需浏览器会话/可能验证码），
复用项目 CDP 基建（带指纹隔离 Chrome + 人工配合过验证）拦截 API 响应采集
全量职业（code/name/category/definition），对齐设计文档 §7.2.3 权威库三源。

用法：
    1. 启动带指纹 Chrome：python -m crawlers.setup_boss_chrome --platform osta
    2. 在弹出浏览器中访问 https://www.osta.org.cn/career，人工通过验证/路由保护
       （如出现验证码手动完成；页面出现职业目录即视为会话就绪）
    3. 运行本脚本（保持浏览器开启）：
       python -m crawlers.osta_cdp_crawler
    脚本连接 CDP（9226）→ 拦截 career API 响应 → 输出 JSONL 到
    data/crawlers/output/osta_occupations_{ts}.jsonl

输出字段：code / name / category / definition / aliases（分号分隔），
对齐 import_occupations.py 的 CSV 格式，可直接 --csv-dir 导入。
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_BACKEND_DIR))

from app.core.logging import setup_logging

logger = setup_logging("osta_cdp_crawler")

DEFAULT_CDP_PORT = 9226
DEFAULT_CDP_URL = os.environ.get("OSTA_CDP_URL", f"http://127.0.0.1:{DEFAULT_CDP_PORT}")
OUTPUT_DIR = _BACKEND_DIR / "data" / "crawlers" / "output"
TARGET_URL = "https://www.osta.org.cn/career"

# 拦截的职业 API 路径（SPA 内部请求，index11 chunk 定位）
API_PATTERNS = ("/client/career/query", "/client/get/tree", "/client/career/detail", "/client/subordinate/data")

# 合规声明（与其余 CDP 爬虫一致）：仅用于竞赛演示，不商用
# 注意：header 值须 ASCII（Playwright APIRequestContext 校验非 ASCII 头会报错）
_COMPLIANCE = {
    "annotation": "competition-demo-only-non-commercial",
    "source": "https://www.osta.org.cn/career",
}


def _normalize(record: dict) -> dict:
    """API 原始记录 → occupations 表字段（code/name/category/definition/aliases）。

    OSTA career API 字段：careerCode / careerName / parentCode / children（递归树），
    definition 由 detail API 补充（tree 无该字段）。
    """
    code = str(record.get("careerCode") or record.get("code") or record.get("id") or "")
    name = str(record.get("careerName") or record.get("name") or record.get("title") or "")
    category = str(record.get("categoryName") or record.get("category") or "")
    definition = str(record.get("careerDesc") or record.get("definition") or record.get("workContent") or "")
    aliases = record.get("alias") or record.get("aliases") or ""
    if isinstance(aliases, list):
        aliases = ";".join(str(a) for a in aliases)
    return {
        "code": code,
        "name": name,
        "category": category,
        "definition": definition,
        "aliases": str(aliases),
    }


def _walk_tree(node: dict, collected: dict[str, dict], depth: int = 0) -> None:
    """递归遍历职业树（body → children 嵌套），叶子与中间层均收录。"""
    norm = _normalize(node)
    if norm["code"] and norm["name"]:
        # 中间层（大类/中类/小类）code 为 1 / 1-01 / 1-01-00 格式，叶子为 8 位细类
        collected[norm["code"]] = norm
    for child in node.get("children") or []:
        _walk_tree(child, collected, depth + 1)


async def _fetch_children(request, parent_code: str, version_id: int = 2, collected: dict[str, dict] | None = None) -> dict[str, dict]:
    """递归采集：subordinate/data 逐级查下级，直到叶子（细类职业）。

    树 API 仅返回 8 大类/79 中类/450 小类；细类（1639 职业）需按父 code
    逐级查询（GET /api/client/subordinate/data?careerCode={parent}&versionId=2）。
    """
    if collected is None:
        collected = {}
    url = f"https://www.osta.org.cn/api/client/subordinate/data?careerCode={parent_code}&versionId={version_id}"
    resp = await request.get(url)
    if resp.status != 200:
        logger.warning(f"subordinate 非 200: {resp.status} {parent_code}")
        return collected
    try:
        data = await resp.json()
    except Exception:
        return collected
    body = data.get("body") if isinstance(data, dict) else None
    if not isinstance(body, list):
        return collected
    for it in body:
        norm = _normalize(it)
        if norm["code"] and norm["name"]:
            collected[norm["code"]] = norm
        # 递归下级（细类还有子级的继续查）
        await _fetch_children(request, norm["code"], version_id, collected)
    return collected


async def run(cdp_url: str, timeout_sec: int) -> int:
    from playwright.async_api import async_playwright

    collected: dict[str, dict] = {}
    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(cdp_url)
        except Exception as e:
            logger.error(f"❌ CDP 连接失败（{cdp_url}）: {e}")
            logger.info("请先运行：python -m crawlers.setup_boss_chrome --platform osta")
            return 0

        context = await browser.new_context()
        await context.set_extra_http_headers({"X-Collection-Purpose": _COMPLIANCE["annotation"]})
        # 复制主 context cookies：人工已在浏览器完成验证/会话，新 context 继承
        # 避免 302 路由保护（与 maimai_cdp_crawler 同模式）
        try:
            main_context = browser.contexts[0]
            cookies = await main_context.cookies()
            if cookies:
                await context.add_cookies(cookies)
                logger.info(f"已继承会话 cookies: {len(cookies)} 个")
        except Exception as e:
            logger.warning(f"cookies 复制失败（可能无会话）: {e}")
        page = await context.new_page()

        # 拦截职业 API 响应（SPA 内部请求）
        async def on_response(resp):
            url = resp.url
            if not any(p in url for p in API_PATTERNS):
                return
            try:
                data = await resp.json()
            except Exception:
                return
            if resp.status != 200:
                logger.warning(f"⚠️ API 非 200: {resp.status} {url}")
                return
            # body 为职业树（递归 children）——全树遍历收录（树仅到小类）
            body = data.get("body") if isinstance(data, dict) else None
            if isinstance(body, list):
                for root in body:
                    _walk_tree(root, collected)
                logger.info(f"✅ 捕获 {url.split('?')[0]} → 累计 {len(collected)} 条")
                return
            # 非树结构（列表型）兜底
            items = []
            if isinstance(data, dict):
                for key in ("records", "list", "rows", "data", "children", "nodes"):
                    v = data.get(key)
                    if isinstance(v, list):
                        items.extend(v)
                        break
            elif isinstance(data, list):
                items = data
            for it in items:
                if isinstance(it, dict):
                    norm = _normalize(it)
                    if norm["code"] and norm["name"]:
                        collected[norm["code"]] = norm
            logger.info(f"✅ 捕获 {url.split('?')[0]} → 累计 {len(collected)} 条")

        page.on("response", on_response)
        logger.info(f"打开职业目录页: {TARGET_URL}")
        await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=30000)

        # 等待人工配合：若 302 保护/验证码，用户需在浏览器完成会话
        deadline = time.time() + timeout_sec
        while time.time() < deadline and len(collected) < 10:
            await asyncio.sleep(2)
        if len(collected) == 0:
            logger.warning(
                "未捕获职业数据——可能是 302 路由保护/验证码。请在弹出的浏览器中"
                "访问职业目录页并完成人工验证，然后重新运行本脚本（--timeout 调大）"
            )
            await context.close()
            return 0

        # 已就绪：树已捕获（8 大类/79 中类/450 小类）——递归 subordinate/data
        # 补全细类（1639 职业，树 API 不含）——用 context.request 继承会话直查
        logger.info("开始递归补全细类职业（subordinate/data 逐级）…")
        roots = sorted(k for k in collected if k.count("-") == 0)  # 8 个大类根
        for root_code in roots:
            await _fetch_children(context.request, root_code, 2, collected)
        logger.info(f"递归完成 → 累计 {len(collected)} 条")
        await context.close()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = OUTPUT_DIR / f"osta_occupations_{ts}.jsonl"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for rec in collected.values():
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    logger.info(f"采集完成: {len(collected)} 条 → {out}")
    return len(collected)


def main() -> None:
    parser = argparse.ArgumentParser(description="OSTA 职业目录 CDP 采集器")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL, help="CDP 端点（默认 9226）")
    parser.add_argument("--timeout", type=int, default=180, help="采集窗口秒数（默认 180，人工验证后调大）")
    args = parser.parse_args()
    n = asyncio.run(run(args.cdp_url, args.timeout))
    print(f"\n采集 {n} 条。CSV 转换：python -c \"...\" 或直接 --csv-dir 导入")


if __name__ == "__main__":
    main()
