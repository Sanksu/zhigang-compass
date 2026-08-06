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
    python -m crawlers.setup_boss_chrome --platform monster --verify   # noVNC 人工验证环境
    python -m crawlers.setup_boss_chrome --platform monster --verify-stop  # 关闭验证环境

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

# 隔离 Chrome profile 根目录（每 CDP 平台独立 profile，互不共享登录态/验证状态）
_CHROME_PROFILE_ROOT = Path.home() / ".zhigang-compass"


def platform_profile_dir(platform: str) -> Path:
    """返回指定 CDP 平台的隔离 Chrome profile 目录（含登录态/验证状态）。"""
    return _CHROME_PROFILE_ROOT / f"{platform}-chrome-profile"


# BOSS 默认 profile（向后兼容；其他平台经 platform_profile_dir 取各自目录）
BOSS_CHROME_PROFILE_DIR = platform_profile_dir("boss")

# 各 CDP 平台独立浏览器端口（与 spider/crawler 默认值对齐，互不共享）
CDP_PORT_BY_PLATFORM = {"boss": 9222, "monster": 9223, "glassdoor": 9224, "maimai": 9225}

# CDP 默认端口
DEFAULT_CDP_PORT = 9222

# 风控环境（monster DataDome 等）对 headless 默认 UA 直接 403，统一覆盖为
# Windows Chrome UA。验证浏览器与采集浏览器必须一致，验证态（cookie）才可复用。
_DESKTOP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")

# 各平台验证/采集默认打开的首个页面（noVNC 人工验证时打开搜索页触发风控）
_VERIFY_URL_BY_PLATFORM = {
    "monster": "https://www.monster.com/jobs/search?q=Python&where=New+York",
    "glassdoor": "https://www.glassdoor.com/Job/jobs.htm?sc.keyword=Python",
    "boss": "https://www.zhipin.com/",
    "maimai": "https://maimai.cn/",
}

# noVNC 人工验证环境（monster DataDome 等会检测 CDP/DevTools 调试连接并强制
# 403，自动方案全失效；改由容器内 Xvfb 虚拟显示 + 无 CDP 有头 chromium，
# 经 noVNC 暴露给用户人工完成验证）
_VERIFY_DISPLAY = ":99"
_VERIFY_VNC_PORT = 5900
_VERIFY_HTTP_PORT = 6080


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
        # Debian 正式 chromium（apt 安装）优先于 playwright 的 Chrome for Testing：
        # DataDome 等风控能从 DOM 横幅识别 CfT（"Chrome for Testing ... automated testing"）
        "/usr/lib/chromium/chromium",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return candidates[0]


def start_chrome(cdp_port: int = DEFAULT_CDP_PORT, cdp_address: str = "127.0.0.1",
                 url: str = "https://www.zhipin.com/", profile_dir: Path | None = None):
    """启动隔离 Chrome（独立 profile + 独立 CDP 端口）。

    Args:
        cdp_port: CDP 调试端口
        cdp_address: CDP 监听地址。Linux 容器部署需局域网连接时设为 0.0.0.0
            （端口由 Docker 暴露），默认 127.0.0.1 仅本机可连
        url: 打开的首个页面
        profile_dir: 该平台的隔离 profile 目录（None 时用 BOSS 默认）
    """
    chrome_path = find_chrome()
    profile_dir = profile_dir or BOSS_CHROME_PROFILE_DIR
    profile_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        chrome_path,
        f"--remote-debugging-port={cdp_port}",
        f"--remote-debugging-address={cdp_address}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        # Chrome 111+ 限制 CDP WebSocket 来源，不加则 playwright connect_over_cdp 报
        # "Failed to open a new tab"（Target.createTarget 被拒）
        "--remote-allow-origins=*",
        url,
    ]
    # Linux 容器内 Debian chromium 需 --no-sandbox --no-zygote：Docker 默认
    # seccomp 禁止 namespace 操作，SUID sandbox 起不来会 FATAL 崩溃（实测）
    if sys.platform != "win32":
        cmd.append("--no-sandbox")
        cmd.append("--no-zygote")
        # 容器内 /dev/shm 默认仅 64MB，headless 渲染大页面（glassdoor 首页）时
        # renderer 因共享内存不足崩溃（实测 Page crashed）；改用 /tmp 内存文件
        cmd.append("--disable-dev-shm-usage")
    # 容器无显示服务器时以无头模式运行（compose 设 CDP_HEADLESS=1；本地桌面保持有头以完成登录）
    if os.environ.get("CDP_HEADLESS") == "1":
        cmd.append("--headless")
        # headless 默认 UA 含 HeadlessChrome，被 glassdoor 等风控拦截（实测 403）；
        # 覆盖为 Windows Chrome UA，crawler 的隔离 context 不设 UA 也继承真实指纹
        cmd.append("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36")
    # 国际源（glassdoor 等）需走代理：CDP_PROXY 非空时注入 --proxy-server。
    # 容器内经 host.docker.internal 指向宿主机 Clash；本地桌面直连则不设。
    cdp_proxy = os.environ.get("CDP_PROXY")
    if cdp_proxy:
        cmd.append(f"--proxy-server={cdp_proxy}")

    print(f"Chrome 路径: {chrome_path}")
    print(f"隔离 profile: {profile_dir}")
    print(f"CDP 端点: http://{cdp_address}:{cdp_port}")
    print(f"打开 URL: {url}")
    print("-" * 60)
    print("请在弹出的 Chrome 窗口中：")
    print("  1. 登录 zhipin.com（扫码/账号密码）")
    print("  2. 完成安全验证（如有）")
    print("  3. 正常浏览几页岗位（缓解风控）")
    print("  4. 保持 Chrome 开启，回到终端运行爬虫")
    print("-" * 60)

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"Chrome 已启动 (PID={proc.pid})")
    print(f"后续运行爬虫时，设置环境变量：$env:BOSS_CDP_URL=http://{cdp_address}:{cdp_port}")
    return proc


def start_verify_browser(platform: str):
    """启动该平台的 noVNC 人工验证环境（Xvfb + 无 CDP 有头 chromium）。

    monster 的 DataDome 会检测 CDP/DevTools 调试连接（console 提示 "Please
    close the DevTools panel" 并强制 403），playwright 等自动方案全部失效。
    本函数在容器内拉起 Xvfb 虚拟显示 + 无 CDP 的有头 chromium，经 x11vnc +
    websockify 暴露为 noVNC 页面，由用户人工完成验证；验证态 cookies 落盘到
    该平台 profile，供 headless CDP 采集复用（UA/代理须一致，见 _DESKTOP_UA）。
    """
    profile_dir = platform_profile_dir(platform)
    profile_dir.mkdir(parents=True, exist_ok=True)

    # 幂等：残留的验证进程会占用 display/端口，且验证浏览器与采集浏览器共用
    # profile（Chromium 同 profile 加锁），重复启动前先清理
    stop_verify_browser(platform)

    chrome_path = find_chrome()
    url = _VERIFY_URL_BY_PLATFORM.get(platform, "https://www.google.com/")

    # 1. Xvfb 虚拟显示
    subprocess.Popen(
        ["Xvfb", _VERIFY_DISPLAY, "-screen", "0", "1920x1080x24", "-nolisten", "tcp"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(1)

    # 2. 有头 chromium：不设 --remote-debugging-port（CDP 会被 DataDome 识别），
    #    不设 --headless；强制 Windows Chrome UA + 走 CDP_PROXY 代理，保证
    #    验证态与采集态指纹一致。
    #    --no-sandbox --no-zygote：Docker 默认 seccomp 禁止 namespace 操作，
    #    Debian chromium 的 SUID sandbox 起不来会 FATAL 崩溃（实测），只能用
    #    该组合（Debian 容器内标准跑法）。--no-sandbox 会显示
    #    "unsupported command-line flag" 警示条，但实测 DataDome 对 monster
    #    的拦截由 IP 信誉主导，警示条不构成决定性因素（住宅 IP 下带警示条
    #    仍可出滑块页）
    cmd = [
        chrome_path,
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--no-sandbox",
        "--no-zygote",
        "--disable-dev-shm-usage",
        f"--user-agent={_DESKTOP_UA}",
        "--window-size=1920,1080",
        url,
    ]
    cdp_proxy = os.environ.get("CDP_PROXY")
    if cdp_proxy:
        cmd.append(f"--proxy-server={cdp_proxy}")
    subprocess.Popen(cmd, env=dict(os.environ, DISPLAY=_VERIFY_DISPLAY),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 3. x11vnc（把 Xvfb 显示导出为 VNC 端口）
    subprocess.Popen(
        ["x11vnc", "-display", _VERIFY_DISPLAY, "-forever", "-shared", "-nopw",
         "-rfbport", str(_VERIFY_VNC_PORT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    # 4. websockify：VNC → WebSocket，配合 noVNC 前端在浏览器里查看
    subprocess.Popen(
        ["websockify", "--web", "/usr/share/novnc", str(_VERIFY_HTTP_PORT),
         f"localhost:{_VERIFY_VNC_PORT}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    time.sleep(2)

    print(f"noVNC 人工验证环境已启动（platform={platform}）")
    print(f"  浏览器地址: http://localhost:{_VERIFY_HTTP_PORT}/vnc.html")
    print(f"  1. 在浏览器打开上述地址，进入容器内 Chromium 桌面")
    print(f"  2. 在打开的 {url} 完成 DataDome 人机验证")
    print("  3. 验证通过（页面正常显示结果）后，运行 --verify-stop 关闭本环境")
    print("  4. 验证态 cookies 已存入 profile，运行爬虫即可复用")


def stop_verify_browser(platform: str):
    """关闭该平台的 noVNC 人工验证环境（chromium / x11vnc / websockify / Xvfb）。

    chromium 按平台 profile 目录精确匹配（避免误杀其他平台的浏览器）；
    其余进程按 display/端口匹配（验证环境独占该虚拟显示与端口）。
    """
    profile_dir = platform_profile_dir(platform)
    os.system(f"pkill -f '{profile_dir}'")
    os.system(f"pkill -f 'Xvfb {_VERIFY_DISPLAY}'")
    os.system(f"pkill -f 'x11vnc.*{_VERIFY_DISPLAY}'")
    os.system(f"pkill -f 'websockify.*{_VERIFY_HTTP_PORT}'")
    print(f"已关闭 {platform} 的 noVNC 验证环境")


def check_cdp(cdp_url: str, quiet: bool = False) -> bool:
    """检查 CDP 端点是否可用（Chrome 是否已启动）。quiet=True 时不打印（供轮询）。"""
    import urllib.request
    import json

    try:
        with urllib.request.urlopen(f"{cdp_url}/json/version", timeout=5) as resp:
            data = json.loads(resp.read().decode())
            if not quiet:
                print(f"CDP 连接成功: {data.get('Browser', 'unknown')}")
            return True
    except Exception as e:
        if not quiet:
            print(f"CDP 连接失败: {e}")
            print(f"请先运行: python -m crawlers.setup_boss_chrome")
        return False


def ensure_cdp_chrome(cdp_url: str | None = None, wait_seconds: int = 20,
                      profile_dir: Path | None = None, url: str | None = None) -> bool:
    """确保 CDP Chrome 可用：不可用时自动启动并轮询就绪。

    CDP 爬虫（BOSS/Monster/Glassdoor/脉脉）发占位请求前调用，避免 Chrome
    被系统/环境回收后爬虫直接失败。profile_dir 传入各平台独立目录，
    保证各爬虫使用自己的浏览器实例（登录态/验证状态互不污染）。

    Args:
        url: 启动时打开的平台首页（便于用户完成登录/风控验证）。
            None 时打开 about:blank（不干扰用户浏览）。
    """
    import os
    import time
    from urllib.parse import urlparse

    cdp_url = cdp_url or os.environ.get("BOSS_CDP_URL", f"http://127.0.0.1:{DEFAULT_CDP_PORT}")
    if check_cdp(cdp_url, quiet=True):
        return True
    # 从 cdp_url 解析端口：各平台独立端口（9222/9223/9224/9225），
    # 拉起的独立 Chrome 必须与爬虫连接的端点一致，否则采集连接失败
    cdp_port = urlparse(cdp_url).port or DEFAULT_CDP_PORT
    start_chrome(cdp_port=cdp_port, url=url or "about:blank", profile_dir=profile_dir)
    for _ in range(wait_seconds * 2):
        time.sleep(0.5)
        if check_cdp(cdp_url, quiet=True):
            return True
    return False


def check_login(cdp_url: str) -> bool:
    """通过 API 调用检查登录态是否有效。"""
    if not check_cdp(cdp_url):
        return False

    import asyncio
    from playwright.async_api import async_playwright

    async def _check():
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(cdp_url)
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


def stop_chrome(cdp_url: str, profile_dir: Path | None = None):
    """关闭专用 Chrome（仅本脚本启动的隔离 Chrome，不误杀用户其他浏览器）。

    优先经 CDP 关闭；失败时按隔离 profile 目录匹配进程（精确匹配，而非 taskkill 全部 chrome.exe）。
    """
    import urllib.request

    profile_dir = profile_dir or BOSS_CHROME_PROFILE_DIR
    try:
        with urllib.request.urlopen(f"{cdp_url}/json/close") as resp:
            print("Chrome CDP 已关闭")
    except Exception:
        if sys.platform == "win32":
            profile_str = str(profile_dir).replace("\\", "\\\\")
            ps = (
                f"Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" "
                f"| Where-Object {{ $_.CommandLine -like '*{profile_str}*' }} "
                f"| ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}"
            )
            os.system(f'powershell -NoProfile -Command "{ps}"')
        else:
            os.system(f"pkill -f '{profile_dir}'")
        print(f"已关闭隔离 Chrome（profile: {profile_dir}）")


def main():
    parser = argparse.ArgumentParser(
        description="CDP 隔离浏览器管理（每平台独立浏览器：独立 profile + 独立端口）"
    )
    parser.add_argument("--platform", default="boss", choices=sorted(CDP_PORT_BY_PLATFORM),
                        help="CDP 平台（决定独立 profile 与默认端口）：boss/monster/glassdoor/maimai")
    parser.add_argument("--check", action="store_true", help="检查 CDP 连接 + 登录态（boss 专用）")
    parser.add_argument("--stop", action="store_true", help="关闭该平台的隔离 Chrome")
    parser.add_argument("--verify", action="store_true",
                        help="启动 noVNC 人工验证环境（Xvfb + 无 CDP 有头 chromium）")
    parser.add_argument("--verify-stop", action="store_true",
                        help="关闭 noVNC 人工验证环境")
    parser.add_argument("--cdp-url", default=None, help="CDP 调试端点（默认按平台端口）")
    parser.add_argument("--cdp-port", type=int, default=None, help="启动时 CDP 端口（默认按平台）")
    parser.add_argument("--cdp-address", default="127.0.0.1",
                        help="启动时 CDP 监听地址（Linux 容器局域网部署设 0.0.0.0）")
    args = parser.parse_args()

    profile_dir = platform_profile_dir(args.platform)
    cdp_port = args.cdp_port or CDP_PORT_BY_PLATFORM[args.platform]
    cdp_url = args.cdp_url or f"http://127.0.0.1:{cdp_port}"

    if args.check:
        check_login(cdp_url)
    elif args.stop:
        stop_chrome(cdp_url, profile_dir=profile_dir)
    elif args.verify:
        start_verify_browser(args.platform)
    elif args.verify_stop:
        stop_verify_browser(args.platform)
    else:
        start_chrome(cdp_port, args.cdp_address, profile_dir=profile_dir)


if __name__ == "__main__":
    main()
