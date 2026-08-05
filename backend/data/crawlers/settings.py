"""爬虫全局配置。

招聘平台分级：
- A 级（稳定源）：BOSS / 智联 / Monster — 主采集通道
- B 级（补充源）：Indeed / Glassdoor — 补充数据
- C 级（实验性）：脉脉 / LinkedIn 公开页 — 合规限制，低频采集

非招聘数据源（课程/论文/社区，用于学习路径与技术热点观察池）：
- 课程平台：icourse163（国内直连）/ coursera / edx（国际代理）
- 论文：arxiv（官方 API，1 req/3s）
- 社区：github trending / stackoverflow（公开页爬取，无需 token）

环境变量：
- BOSS_COOKIES: BOSS 直聘登录 Cookie（必填，格式 "k1=v1; k2=v2"）
  获取方式：浏览器登录 zhipin.com → F12 → Application → Cookies → 复制
- HTTP_PROXY / HTTPS_PROXY: 国际平台代理（可选）
"""

import os

# ---------- 采集量目标 ----------
DAILY_P0_DOMESTIC = 60    # 国内最低
DAILY_P0_INTERNATIONAL = 40  # 国际最低
DAILY_P1_TOTAL = 200       # 挑战目标

# ---------- BOSS 直聘登录态 ----------
# BOSS 搜索 API /wapi/zpgeek/search/joblist.json 需要登录态
# 用户需主动登录 zhipin.com 后导出 Cookie，通过环境变量注入
BOSS_COOKIES = os.environ.get("BOSS_COOKIES", "")

# ---------- 请求控制 ----------
DEFAULT_DOWNLOAD_DELAY = 2          # 秒，默认请求间隔
RANDOMIZE_DOWNLOAD_DELAY = True     # 随机化延迟
CONCURRENT_REQUESTS = 8             # 全局并发
CONCURRENT_REQUESTS_PER_DOMAIN = 2  # 单域名并发

# 平台级速率限制
RATE_LIMIT = {
    # 招聘平台
    "boss":     {"req_per_min": 20,  "delay_range": (2, 5)},
    "zhilian":  {"req_per_min": 20,  "delay_range": (2, 5)},
    "monster":  {"req_per_min": 30,  "delay_range": (1, 3)},
    "indeed":   {"req_per_min": 15,  "delay_range": (2, 6)},
    "glassdoor":{"req_per_min": 15,  "delay_range": (2, 6)},
    "maimai":   {"req_per_min": 5,   "delay_range": (10, 20)},     # ≤100 req/h
    "linkedin_public": {"req_per_min": 5, "delay_range": (10, 20)},
    # 课程平台（每周全量同步，限流宽松）
    "icourse163": {"req_per_min": 10, "delay_range": (6, 12)},     # 国内直连，反爬较严
    "coursera":   {"req_per_min": 20, "delay_range": (3, 6)},      # 国际代理
    "edx":        {"req_per_min": 20, "delay_range": (3, 6)},      # 国际代理
    # 论文（arXiv 官方约束 1 req/3s = 20 req/min）
    "arxiv":      {"req_per_min": 20, "delay_range": (3, 5)},
    # 社区（公开页爬取，保守限速）
    "github":     {"req_per_min": 10, "delay_range": (6, 12)},     # trending 公开页
    "stackoverflow": {"req_per_min": 10, "delay_range": (6, 12)},  # 标签页公开页
}

# ---------- 代理池 ----------
# 国际平台需走代理；国内直连（platform 不在 POOL_REQUIRED 中时）
POOL_REQUIRED = {
    # 招聘平台
    "indeed", "monster", "glassdoor", "linkedin_public",
    # 课程平台（icourse163 国内直连，不在代理池）
    "coursera", "edx",
    # 论文与社区（国际源）
    "arxiv", "github", "stackoverflow",
}

# 开发环境默认代理（本地 Clash/V2Ray 默认端口；生产以 PROXY_POOL/API 配置为准）
# 中间件回退链：PROXY_POOL → 环境变量 HTTPS_PROXY/HTTP_PROXY → DEFAULT_PROXY
DEFAULT_PROXY = "http://127.0.0.1:7890"

# 代理池列表（可扩展：支持 HTTP/HTTPS/SOCKS5）
# 格式: scheme://user:pass@host:port
PROXY_POOL = [
    # 示例代理（开发环境使用，生产替换为真实代理池）
    # 可从代理服务商 API 动态获取后注入此列表
]

# 代理池刷新间隔（秒），PROXY_POOL 为空时每此调用重新获取
PROXY_POOL_REFRESH_INTERVAL = 300  # 5 分钟

# 代理池自动刷新接口（可选，返回 JSON 代理列表）
PROXY_POOL_API_URL = ""  # 留空则使用静态 PROXY_POOL
PROXY_POOL_API_KEY = ""

# 单个代理的最大失败次数，超过后自动剔除
PROXY_MAX_FAILURES = 3

# ---------- 重试 ----------
RETRY_TIMES = 3
RETRY_HTTP_CODES = [429, 500, 502, 503, 504]

# ---------- 独立采集脚本 ----------
# subprocess 调用（CDP/JobSpy/Playwright 独立脚本）的最大等待秒数，超时后终止子进程。
# 300s：boss 默认 max_pages=5 每任务约 1-3min，足够覆盖正常任务；900s 会让异常任务
# 长期占用且串行放大（8 关键词 × 5 城市最坏 40×900s）。
SUBPROCESS_TIMEOUT = 300

# ---------- 脉脉合规 ----------
MAIMAI_COMPLIANCE = {
    "annotation": "用于竞赛演示不商用",           # 采集声明
    "desensitization": True,                      # 数据脱敏
    "rate_limit": 100,                            # ≤100 req/h
    "schedule_hours": (22, 8),                    # 夜间运行 22:00-08:00
}

# ---------- 输出 ----------
FEED_EXPORT_ENCODING = "utf-8"
FEED_FORMAT = "jsonlines"
