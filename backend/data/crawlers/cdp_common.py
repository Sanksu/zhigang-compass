"""CDP 爬虫共享工具（08-17 收敛 glassdoor/monster 两处相同连接与隔离逻辑）。

前置：用户已运行 setup_boss_chrome.py 启动带 CDP 的 Chrome/Edge。
"""

import logging

logger = logging.getLogger(__name__)


async def connect_cdp(p, cdp_url: str):
    """连接已启动的 CDP 浏览器；失败记日志返回 None（调用方 return 0 退出）。"""
    try:
        return await p.chromium.connect_over_cdp(cdp_url)
    except Exception as e:
        logger.error(f"❌ CDP 连接失败（{cdp_url}）: {e}")
        logger.info("   请先运行 setup_boss_chrome.py 启动带 CDP 的 Chrome/Edge")
        return None


async def isolated_page(browser):
    """新建隔离 context+page，并复制主 context 的 cookies（保留站点验证态）。

    爬虫导航只发生在隔离 context 内，不触碰用户正在浏览的页面；
    cookies 复制失败仅告警（不阻断采集）。
    """
    context = await browser.new_context()
    if browser.contexts:
        try:
            _cookies = await browser.contexts[0].cookies()
            if _cookies:
                await context.add_cookies(_cookies)
            else:
                logger.warning("⚠️ 主 context 无 cookies（站点验证可能未完成）")
        except Exception as e:
            logger.warning(f"⚠️ 复制 cookies 到隔离 context 失败: {e}")
    return context, await context.new_page()
