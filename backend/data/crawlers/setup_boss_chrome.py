"""BOSS 直聘专用 Chrome 启动脚本（CDP 模式）。

参考 github.com/eatmoreduck/boss-zhipin-scraper 的 --setup-chrome 方案：
- 启动隔离 Chrome（独立 user-data-dir），不污染主 Chrome
- 开启 CDP 远程调试端口（默认 9222）
- 用户在弹出的 Chrome 中手动登录 zhipin.com
- 登录态持久保存到隔离目录，后续爬虫通过 CDP 连接复用登录态

用法：
    python -m crawlers.setup_boss_chrome              # 启动并等待登录
    python -m crawlers.setup_boss_chrome --check      # 检查 CDP + 登录态
    python -m crawlers.setup_boss_chrome --stop       # 关闭专用 Chrome

为什么不用 Playwright 启动浏览器？
    Playwright/Selenium 启动的浏览器指纹与真实 Chrome 不同，BOSS 风控会识别
    并返回 code=36/37。通过 CDP 连接用户已登录的真实 Chrome，复用真实指纹，
    是目前最稳定的方案。
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

# 隔离 Chrome profile 目录（持久保存登录态）
BOSS_CHROME_PROFILE_DIR = Path.home() / ".zhigang-compass" / "boss-chrome-profile"

# CDP 默认端口
DEFAULT_CDP_PORT = 9222


def find_chrome() -> str:
    """查找 Chromium 内核浏览器（Chrome 优先，Edge 次之）。"""
    if sys.platform == "win32":
        candidates = []
        # Chrome
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(Path(local_app_data) / "Google" / "Chrome" / "Application" / "chrome.exe")
        for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
            base = os.environ.get(env_name)
            if base:
                candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
        # Edge（Chromium 内核，支持 CDP）
        for env_name in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
            base = os.environ.get(env_name)
            if base:
                candidates.append(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(Path(local_app_data) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
        for c in candidates:
            if c.exists():
                return str(c)
        return str(candidates[0]) if candidates else "chrome.exe"
    # macOS / Linux
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/usr/bin/google-chrome",
        "/usr/bin/microsoft-edge",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return candidates[0]


def start_chrome(cdp_port: int = DEFAULT_CDP_PORT, url: str = "https://www.zhipin.com/"):
    """启动隔离 Chrome 并打开 BOSS 直聘登录页。"""
    chrome_path = find_chrome()
    profile_dir = BOSS_CHROME_PROFILE_DIR
    profile_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        chrome_path,
        f"--remote-debugging-port={cdp_port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ]

    print(f"Chrome 路径: {chrome_path}")
    print(f"隔离 profile: {profile_dir}")
    print(f"CDP 端口: {cdp_port}")
    print(f"打开 URL: {url}")
    print("-" * 60)
    print("请在弹出的 Chrome 窗口中：")
    print("  1. 登录 zhipin.com（扫码/账号密码）")
    print("  2. 完成安全验证（如有）")
    print("  3. 正常浏览几页岗位（缓解风控）")
    print("  4. 保持 Chrome 开启，回到终端运行爬虫")
    print("-" * 60)

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"Chrome 已启动 (PID={proc.pid})，CDP 端口 {cdp_port}")
    print(f"后续运行爬虫时，设置环境变量：$env:BOSS_CDP_PORT={cdp_port}")
    return proc


def check_cdp(cdp_port: int = DEFAULT_CDP_PORT) -> bool:
    """检查 CDP 端口是否可用（Chrome 是否已启动）。"""
    import urllib.request
    import json

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json/version", timeout=5) as resp:
            data = json.loads(resp.read().decode())
            print(f"CDP 连接成功: {data.get('Browser', 'unknown')}")
            return True
    except Exception as e:
        print(f"CDP 连接失败: {e}")
        print(f"请先运行: python -m crawlers.setup_boss_chrome")
        return False


def check_login(cdp_port: int = DEFAULT_CDP_PORT) -> bool:
    """通过 API 调用检查登录态是否有效。"""
    if not check_cdp(cdp_port):
        return False

    import asyncio
    from playwright.async_api import async_playwright

    async def _check():
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await context.new_page()

            # 先导航到 zhipin.com 域下，否则 fetch 会跨域失败
            try:
                await page.goto("https://www.zhipin.com/", wait_until="domcontentloaded", timeout=15000)
            except Exception as e:
                print(f"❌ 导航到 zhipin.com 失败: {e}")
                await page.close()
                await browser.close()
                return False

            # 调用 BOSS API 检查登录态
            api_url = "https://www.zhipin.com/wapi/zpgeek/search/joblist.json?scene=1&query=Python&city=101010100&page=1"
            try:
                raw = await page.evaluate("""
                    async (apiUrl) => {
                        const r = await fetch(apiUrl, {credentials: 'include'});
                        return await r.text();
                    }
                """, api_url)
                import json
                data = json.loads(raw)
                code = data.get("code")
                if code == 0:
                    jobs = (data.get("zpData") or {}).get("jobList") or []
                    print(f"✅ 登录态有效，API 返回 {len(jobs)} 条岗位")
                    return True
                else:
                    print(f"❌ API 返回错误: code={code}, message={data.get('message', '')}")
                    if code == 36:
                        print("   账户被风控，请在 Chrome 中完成安全验证后重试")
                    elif code == 37:
                        print("   环境异常/Cookie 失效，请在 Chrome 中重新登录")
                    return False
            except Exception as e:
                print(f"❌ API 调用失败: {e}")
                return False
            finally:
                await page.close()
                await browser.close()

    return asyncio.run(_check())


def stop_chrome(cdp_port: int = DEFAULT_CDP_PORT):
    """关闭专用 Chrome（按 CDP 端口匹配）。"""
    import urllib.request
    import json

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json/close") as resp:
            print("Chrome CDP 已关闭")
    except Exception:
        # /json/close 可能不支持，用 taskkill
        if sys.platform == "win32":
            os.system(f"taskkill /F /IM chrome.exe /FI \"WINDOWTITLE eq BOSS*\"")
        print(f"如需手动关闭，请在任务管理器中结束 Chrome 进程（CDP 端口 {cdp_port}）")


def main():
    parser = argparse.ArgumentParser(description="BOSS 直聘专用 Chrome 管理（CDP 模式）")
    parser.add_argument("--check", action="store_true", help="检查 CDP 连接 + 登录态")
    parser.add_argument("--stop", action="store_true", help="关闭专用 Chrome")
    parser.add_argument("--cdp-port", type=int, default=DEFAULT_CDP_PORT, help="CDP 端口")
    args = parser.parse_args()

    if args.check:
        check_login(args.cdp_port)
    elif args.stop:
        stop_chrome(args.cdp_port)
    else:
        start_chrome(args.cdp_port)


if __name__ == "__main__":
    main()
