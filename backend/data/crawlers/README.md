# crawlers

招聘平台 + 课程/论文/社区爬虫模块。基于 Scrapy + Playwright + CDP。

- **招聘平台**：13 源，A/B/C 三级分级
- **非招聘数据源**：课程 3 个（icourse163/Coursera/edX）+ 论文 1 个（arXiv）+ 社区 2 个（GitHub Trending/Stack Overflow），用于学习路径构建与技术热点观察池

## 目录结构

```
crawlers/
├── __init__.py
├── scrapy.cfg           # Scrapy 项目配置
├── scrapy_settings.py   # Scrapy 框架设置（Playwright/中间件/管道注册）
├── settings.py          # 业务配置（速率/代理/合规/重试，含课程/论文/社区）
├── items.py             # JobItem / CourseItem / PaperItem / CommunityTrendItem
├── pipelines.py         # CleaningPipeline + PostgresPipeline（按 Item 类型路由）
├── middlewares.py       # UA轮换 + 代理池
├── base_spider.py       # BaseSpider 基类（关键字/城市遍历 + JobItem 构造）
├── setup_boss_chrome.py # 隔离 Chrome 启动脚本（CDP 9222，BOSS/Monster/Glassdoor/Maimai 共用）✅
├── boss_cdp_crawler.py  # BOSS 独立采集脚本（CDP + 内部 API）✅
├── jobspy_crawler.py            # JobSpy 独立采集脚本（Indeed/LinkedIn 共用）✅
├── monster_cdp_crawler.py   # Monster 独立采集脚本（CDP + XHR 拦截）✅
├── glassdoor_cdp_crawler.py # Glassdoor 独立采集脚本（CDP + SSR DOM 提取）✅
├── maimai_cdp_crawler.py    # 脉脉独立采集脚本（CDP + 飞书招聘 DOM 提取）✅
├── icourse163_crawler.py    # 中国大学MOOC 独立采集脚本（Playwright + 内部 RPC API）✅
├── output/              # JSONL 输出目录（.gitignore 忽略）
└── spiders/
    ├── boss.py           # A 级 — BOSS 直聘 ✅（subprocess 调 boss_cdp_crawler.py）
    ├── zhilian.py        # A 级 — 智联招聘 ✅
    ├── monster.py        # A 级 — Monster ✅（subprocess 调 monster_cdp_crawler.py）
    ├── indeed.py         # B 级 — Indeed ✅（subprocess 调 jobspy_crawler.py，需代理）
    ├── glassdoor.py      # B 级 — Glassdoor ✅（subprocess 调 glassdoor_cdp_crawler.py）
    ├── maimai.py         # C 级 — 脉脉 ✅（subprocess 调 maimai_cdp_crawler.py，飞书招聘页）
    ├── linkedin_public.py # C 级 — LinkedIn ✅（subprocess 调 jobspy_crawler.py，需代理）
    ├── arxiv.py          # 论文 — arXiv ✅（官方 API + Atom XML，需代理）
    ├── github.py         # 社区 — GitHub Trending ✅（公开页 HTML，需代理）
    ├── stackoverflow.py  # 社区 — Stack Overflow ✅（标签页 HTML，需代理）
    ├── icourse163.py     # 课程 — 中国大学MOOC ✅（Playwright 渲染，国内直连）
    ├── coursera.py       # 课程 — Coursera ✅（Playwright 渲染，需代理）
    └── edx.py            # 课程 — edX ✅（Playwright 渲染，需代理）
```

> **非招聘数据源说明**：课程/论文/社区爬虫不继承 `BaseSpider`（非岗位数据），直接继承 `Scrapy.Spider`，各自构造对应 Item 类。arXiv/GitHub/SO 用普通 HTTP 请求（无 Playwright），icourse163/Coursera/edX 用 Playwright 渲染（反爬保护）。

### 架构说明（2026-07-29 重构）

7 个已贯通爬虫（BOSS / 智联 / Monster / Indeed / LinkedIn / Glassdoor / 脉脉）+ setup_boss_chrome CDP 启动器采用 **「Scrapy Spider + 独立脚本 + subprocess」** 架构：

- **Scrapy Spider**：负责任务编排、关键字/城市遍历、Item 构造与管道消费
- **独立采集脚本**：负责实际 HTTP 请求 / 浏览器自动化
- **subprocess 隔离**：避免 Playwright(asyncio) / JobSpy(同步阻塞) 与 Scrapy Twisted 事件循环冲突

| 平台 | 采集方式 | 参考项目 | 状态 |
|------|----------|----------|------|
| BOSS 直聘 | CDP 连接真实浏览器 + 内部 API `/wapi/zpgeek/search/joblist.json` | [eatmoreduck/boss-zhipin-scraper](https://github.com/eatmoreduck/boss-zhipin-scraper) | ✅ |
| 智联招聘 | Playwright + CSS 选择器（列表页直出） | - | ✅ |
| Monster | CDP 连接真实浏览器 + XHR 拦截 `appsapi.monster.io` | [shahidirfan100/Monster-Job-Scraper](https://github.com/shahidirfan100/Monster-Job-Scraper) | ✅ |
| Indeed | JobSpy 库（解析 JSON-LD 结构化数据） | [speedyapply/JobSpy](https://github.com/speedyapply/JobSpy) | ✅ |
| LinkedIn | JobSpy 库（解析 JSON-LD 结构化数据） | [speedyapply/JobSpy](https://github.com/speedyapply/JobSpy) | ✅ |
| Glassdoor | CDP 连接真实浏览器 + SSR DOM 提取（JSON-LD + data-test 属性） | - | ✅ |
| 脉脉 | CDP 连接真实浏览器 + 飞书招聘页 DOM 提取（maimai.jobs.feishu.cn） | - | ✅ |

BOSS / Monster / Glassdoor / Maimai 共用 CDP 端口 9222（同一时刻只能运行其中一个）。

## 依赖安装

```bash
cd backend
# 在 pyproject.toml 中添加以下依赖后执行
uv sync
uv run playwright install chromium
```

需添加的依赖：
```
scrapy>=2.11
scrapy-playwright>=0.0.40
playwright>=1.40
python-jobspy>=1.1.40   # Indeed/LinkedIn 采集（仅 *_jobspy_crawler.py 使用）
```

> **JobSpy 隔离说明**：`python-jobspy` 与 `transformers` 存在 `regex` 版本冲突，通过 subprocess 调用独立脚本使用，不与主 venv 共享 import 上下文。

## 平台分级

### 招聘平台（A/B/C 三级）

| 等级 | 平台 | 日限制 | 代理 | 状态 |
|------|------|--------|------|------|
| A | BOSS 直聘 | 20 req/min | 直连 | ✅ CDP |
| A | 智联招聘 | 20 req/min | 直连 | ✅ |
| A | Monster | 30 req/min | 代理 | ✅ CDP |
| B | Indeed | 15 req/min | 代理池 | ✅ JobSpy |
| B | Glassdoor | 15 req/min | 代理池 | ✅ CDP |
| C | 脉脉 | 5 req/min (≤100/h) | 直连（夜间） | ✅ CDP（飞书招聘页） |
| C | LinkedIn 公开页 | 5 req/min | 代理池 | ✅ JobSpy |

### 非招聘数据源（课程/论文/社区）

| 类型 | 平台 | 采集方式 | 频率 | 代理 | 用途 | 状态 |
|------|------|---------|------|------|------|------|
| 课程 | 中国大学MOOC | Playwright 渲染搜索页 | 每周 | 直连 | 学习路径（国内） | ✅ |
| 课程 | Coursera | Playwright 渲染搜索页 | 每周 | 代理 | 学习路径（国际） | ✅ |
| 课程 | edX | Playwright 渲染搜索页 | 每周 | 代理 | 学习路径（国际） | ✅ |
| 论文 | arXiv | 官方 API + Atom XML | 每日 | 代理 | 技术热点观察池 | ✅ |
| 社区 | GitHub Trending | 公开页 HTML 解析 | 每日 | 代理 | 技术热点观察池 | ✅ |
| 社区 | Stack Overflow | 标签页 HTML 解析 | 每日 | 代理 | 技术热点观察池 | ✅ |

> **分层源策略**（设计文档 §7.2.2）：arXiv/GitHub/SO 信号**不独立触发 candidate**，仅进「技术热点观察池」作置信度加分（单异常 +0.10 / 双异常 +0.15，封顶 1.0）。课程数据用于构建 `(:Skill)-[:LEARNABLE_VIA]->(:Course)` 关系。

## 采集量目标

- P0: ≥100 条/日（国内≥60 + 国际≥40）
- P1: ≥200 条/日

## 合规说明

- **脉脉**：注明用于竞赛演示不商用 + 数据脱敏 + 限频 ≤100 req/h + 夜间运行 22:00-08:00
- **国内平台**：单 IP 直连，合理频率
- **国际平台**：代理池（PROXY_POOL，自动剔除失败代理 + 定时刷新）
- **全平台**：遵守 robots.txt，仅采集公开搜索页，不绕过登录态
- **非招聘数据源**：仅采集公开元数据（标题/摘要/统计指标），不下载 PDF/视频内容

## 运行

### 前置：CDP 浏览器（BOSS / Monster / Glassdoor / Maimai 共用）

BOSS 直聘、Monster、Glassdoor、脉脉都需先启动带 CDP 的真实浏览器并完成人工验证：

```bash
cd backend/data/crawlers
# 1. 启动隔离 Chrome/Edge（CDP 端口 9222，登录态持久保存到 ~/.zhigang-compass/boss-chrome-profile）
python -m crawlers.setup_boss_chrome

# 2. 在弹出的浏览器中：
#    - 访问 zhipin.com 完成登录（BOSS）
#    - 访问 monster.com 完成 DataDome challenge（Monster）
#    - 访问 glassdoor.com 通过 Cloudflare 验证（Glassdoor，需系统代理）
#    - 浏览器保持开启，爬虫通过 CDP 连接复用登录态

# 3. 检查 CDP + 登录态
python -m crawlers.setup_boss_chrome --check

# 4. 采集完成后关闭浏览器
python -m crawlers.setup_boss_chrome --stop
```

### 各平台采集命令

```bash
cd backend/data/crawlers

# BOSS 直聘（需先完成 CDP 前置 + 浏览器保持开启）
scrapy crawl boss -a keywords=Python,Java -a cities=北京,上海 -o output/boss.jsonl

# 智联招聘（直连，列表页直接产出 Item，详情页有验证码故留空）
scrapy crawl zhilian -a keywords=Python -a cities=北京 -o output/zhilian.jsonl

# Monster（需先完成 CDP 前置 + 浏览器保持开启）
scrapy crawl monster -a keywords=Python -a cities="New York" -o output/monster.jsonl

# Indeed（需 Clash/V2Ray 代理：HTTPS_PROXY=http://127.0.0.1:7890）
$env:HTTPS_PROXY="http://127.0.0.1:7890"
scrapy crawl indeed -a keywords=Python -a cities="New York" -o output/indeed.jsonl

# Glassdoor（需先完成 CDP 前置 + 浏览器配置系统代理）
scrapy crawl glassdoor -a keywords=Python -a cities="New York" -o output/glassdoor.jsonl

# LinkedIn 公开页（需代理）
scrapy crawl linkedin_public -a keywords=Python -a cities="New York" -o output/linkedin_public.jsonl

# 脉脉（仅夜间 22:00-08:00，CDP 浏览器保持开启，无需登录态）
scrapy crawl maimai -a keywords=Python -o output/maimai.jsonl

# ── 非招聘数据源（课程/论文/社区）──

# arXiv 论文（官方 API，需代理，1 req/3s 限速）
$env:HTTPS_PROXY="http://127.0.0.1:7890"
scrapy crawl arxiv -a categories=cs.AI,cs.LG -a max_results=50 -o output/arxiv.jsonl -s ROBOTSTXT_OBEY=False

# GitHub Trending（公开页 HTML，需代理）
$env:HTTPS_PROXY="http://127.0.0.1:7890"
scrapy crawl github -a languages=python,java,javascript -a since=daily -o output/github.jsonl -s ROBOTSTXT_OBEY=False

# Stack Overflow（标签页 HTML，需代理）
$env:HTTPS_PROXY="http://127.0.0.1:7890"
scrapy crawl stackoverflow -a tags=python,machine-learning,java -a tab=Newest -a max_pages=3 -o output/stackoverflow.jsonl -s ROBOTSTXT_OBEY=False

# 中国大学MOOC（Playwright 渲染，国内直连，无需代理）
scrapy crawl icourse163 -a keywords=Python,机器学习,人工智能 -o output/icourse163.jsonl -s ROBOTSTXT_OBEY=False

# Coursera（Playwright 渲染，需代理）
$env:HTTPS_PROXY="http://127.0.0.1:7890"
scrapy crawl coursera -a keywords=Python,Machine-Learning -o output/coursera.jsonl -s ROBOTSTXT_OBEY=False

# edX（Playwright 渲染，需代理）
$env:HTTPS_PROXY="http://127.0.0.1:7890"
scrapy crawl edx -a keywords=Python,Data-Science -o output/edx.jsonl -s ROBOTSTXT_OBEY=False
```

### 常见问题

| 现象 | 原因 | 处理 |
|------|------|------|
| BOSS API 返回 `code=37` | 未登录 / Cookie 未生效 | 重新运行 `setup_boss_chrome`，在浏览器登录后重试 |
| BOSS API 返回 `code=36` | 账户被风控 | 在浏览器完成安全验证后重新导出 Cookie |
| Monster 拦截不到 API 响应 | DataDome 拦截 / 页面未加载完 | 在浏览器手动过 challenge，检查 CDP 端口连通 |
| Glassdoor 拦截不到 GraphQL 响应 | 初始页是 SSR，无 API 调用 | 已改为 SSR DOM 提取，无需拦截 GraphQL |
| Glassdoor 返回 0 条岗位 | Cloudflare 拦截 | 在浏览器先访问 glassdoor.com 完成验证，检查代理 |
| 脉脉返回 0 条岗位 | 飞书招聘页未加载完 | 检查 CDP 端口连通，确认浏览器能访问 maimai.jobs.feishu.cn |
| Indeed 报 `regex` 版本冲突 | JobSpy 与 transformers 冲突 | 已通过 subprocess 隔离；若仍冲突，单独建 venv 跑 JobSpy |
| CDP 连接失败 | 浏览器未启动 / 端口被占 | `python -m crawlers.setup_boss_chrome --check` 排查 |
| arXiv 返回 403 | robots.txt 限制 | 运行时加 `-s ROBOTSTXT_OBEY=False`（arXiv API 路径允许爬取，但根 robots 限制） |
| arXiv 解析到 0 条论文 | API 响应格式变化 / 网络问题 | 检查代理，查看 stderr 日志中的响应前 500 字符 |
| GitHub Trending 返回 0 条 | 页面改版 / Cloudflare 拦截 | 检查代理，对照真实页面验证 `article.Box-row` 选择器 |
| Stack Overflow 返回 0 条 | 页面改版 / Cloudflare 拦截 | 检查代理，对照真实页面验证 `div.s-post-summary` 选择器 |
| Coursera/edX 返回 0 条课程 | Cloudflare 拦截 / Playwright 超时 | 检查代理，延长 `PLAYWRIGHT_DEFAULT_NAVIGATION_TIMEOUT` |
| icourse163 返回 0 条课程 | 反爬拦截 / 页面改版 | 检查 Playwright 是否正常启动，对照真实页面验证选择器 |
