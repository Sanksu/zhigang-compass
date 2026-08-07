# 智岗罗盘 — 本地开发一键启动（api + worker + 可选前端）
#
# 用法：
#   .\script\dev.ps1         # 确保基础设施 + 启动 api + worker
#   .\script\dev.ps1 -Frontend      # 额外启动前端 dev server
#   .\script\dev.ps1 -Restart       # 8000/api/worker 已运行时强制重启（加载最新代码）
#   .\script\dev.ps1 -SkipInfra     # 跳过 docker 基础设施检查
#
# 前置条件：
#   - Docker Desktop 已启动（postgres/redis/neo4j 容器，幂等 up -d）
#   - backend 依赖已装（uv run 可用）；frontend 依赖已装（pnpm 可用）
#
# 设计说明（与团队启动指南/项目记忆一致）：
#   - api 与 worker 是独立进程：api 用 `uv run python -m uvicorn`（坑 2：避免 uv trampoline），
#     worker 用 `uv run python -m arq app.workers.tasks.WorkerSettings`
#   - PYTHONPATH 需含 backend;backend\data（scrapy 爬虫模块在 data/crawlers，坑 15）
#   - 日志写入 logs/（已 gitignore）：api.log / worker.log / frontend.log
#   - 幂等：8000 被占用且未加 -Restart 时不重复启动；worker 已在跑时不重复启动
#
# 注意：
#   - worker 启动即消费 Redis 队列中遗留的 crawl_platform 任务，若队列有历史任务会被执行
#   - 国际源爬虫需 HTTPS_PROXY=http://127.0.0.1:7890（本脚本不设置）

param(
    [switch]$Frontend,
    [switch]$Restart,
    [switch]$SkipInfra
)

$ErrorActionPreference = "Stop"
# 脚本位于 script/ 下，项目根目录为其父目录
$Root = Split-Path $PSScriptRoot -Parent
$BackendDir = Join-Path $Root "backend"
$FrontendDir = Join-Path $Root "frontend"
$LogDir = Join-Path $Root "logs"
$env:PYTHONPATH = "$BackendDir;$BackendDir\data"
# 强制 UTF-8 输出：避免中文 Windows 上 api/worker 日志（含爬虫实时日志）GBK 乱码
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

function Get-Listener($Port) {
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
}

function Get-WorkerProcess {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "arq.*WorkerSettings" }
}

# 循环等待端口/进程就绪（uv run 首次准备 + python 启动较慢，固定 sleep 不可靠）
function Wait-Ready([scriptblock]$Check, [int]$Seconds = 15) {
    for ($i = 0; $i -lt $Seconds; $i++) {
        if (& $Check) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

# ── 1. 基础设施（幂等，已运行保持）──
if (-not $SkipInfra) {
    Write-Host "[1/4] 确保基础设施 postgres/redis/neo4j ..." -ForegroundColor Cyan
    Push-Location $Root
    docker compose up -d postgres redis neo4j
    if ($LASTEXITCODE -ne 0) {
        Write-Host "!! 基础设施启动失败，请确认 Docker Desktop 已运行" -ForegroundColor Red
        Pop-Location
        exit 1
    }
    Pop-Location
}

# ── 2. api（8000）──
$api = Get-Listener 8000
if ($api -and -not $Restart) {
    Write-Host "[2/4] 8000 已有进程（PID $($api.OwningProcess)）在监听，跳过 api 启动。用 -Restart 重启以加载最新代码" -ForegroundColor Yellow
} else {
    if ($api) {
        Write-Host "[2/4] 停止旧 api（PID $($api.OwningProcess)）..." -ForegroundColor Yellow
        Stop-Process -Id $api.OwningProcess -Force
        Start-Sleep -Seconds 1
    }
    Write-Host "[2/4] 启动 api（uvicorn :8000）..." -ForegroundColor Cyan
    $apiProc = Start-Process -FilePath "uv" -ArgumentList @(
        "run", "python", "-m", "uvicorn", "app.main:app",
        "--host", "127.0.0.1", "--port", "8000"
    ) -WorkingDirectory $BackendDir -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir "api.log") `
        -RedirectStandardError (Join-Path $LogDir "api.err.log") -PassThru
    if (-not (Wait-Ready { Get-Listener 8000 })) {
        Write-Host "!! api 启动失败，查看 logs/api.err.log" -ForegroundColor Red
        exit 1
    }
    Write-Host "   api 已启动（PID $($apiProc.Id)）" -ForegroundColor Green
}

# ── 3. worker（ARQ，消费爬虫/简历/ETL 队列）──
$worker = Get-WorkerProcess
if ($worker -and -not $Restart) {
    Write-Host "[3/4] worker 已在运行（PID $($worker.ProcessId)），跳过启动。用 -Restart 重启" -ForegroundColor Yellow
} else {
    if ($worker) {
        Write-Host "[3/4] 停止旧 worker（PID $($worker.ProcessId)）..." -ForegroundColor Yellow
        Stop-Process -Id $worker.ProcessId -Force
        Start-Sleep -Seconds 1
    }
    Write-Host "[3/4] 启动 worker（arq WorkerSettings）..." -ForegroundColor Cyan
    $workerProc = Start-Process -FilePath "uv" -ArgumentList @(
        "run", "python", "-m", "arq", "app.workers.tasks.WorkerSettings"
    ) -WorkingDirectory $BackendDir -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir "worker.log") `
        -RedirectStandardError (Join-Path $LogDir "worker.err.log") -PassThru
    if (-not (Wait-Ready { Get-WorkerProcess })) {
        Write-Host "!! worker 启动失败，查看 logs/worker.err.log" -ForegroundColor Red
        exit 1
    }
    Write-Host "   worker 已启动（PID $($workerProc.Id)）" -ForegroundColor Green
}

# ── 4. 前端（可选）──
if ($Frontend) {
    if (Get-Listener 5173) {
        Write-Host "[4/4] 5173 已有前端 dev server，跳过" -ForegroundColor Yellow
    } else {
        Write-Host "[4/4] 启动前端 dev server（vite :5173）..." -ForegroundColor Cyan
        # pnpm 是 .cmd shim，Start-Process 需显式 .cmd 才能启动（直接写 pnpm 报"非有效 Win32 应用程序"）
        $feProc = Start-Process -FilePath "pnpm.cmd" -ArgumentList @("dev") -WorkingDirectory $FrontendDir `
            -WindowStyle Hidden -RedirectStandardOutput (Join-Path $LogDir "frontend.log") `
            -RedirectStandardError (Join-Path $LogDir "frontend.err.log") -PassThru
        Start-Sleep -Seconds 5
        if (-not (Get-Listener 5173)) {
            Write-Host "!! 前端启动失败，查看 logs/frontend.err.log" -ForegroundColor Red
        } else {
            Write-Host "   前端已启动（PID $($feProc.Id)）" -ForegroundColor Green
        }
    }
}

Write-Host ""
Write-Host "启动完成。服务状态：" -ForegroundColor Cyan
Write-Host "  api    : http://127.0.0.1:8000  (docs: /docs)  PID $((Get-Listener 8000).OwningProcess)"
$wp = Get-WorkerProcess
Write-Host "  worker : PID $($wp.ProcessId)"
if ($Frontend) {
    Write-Host "  frontend: http://localhost:5173  PID $((Get-Listener 5173).OwningProcess)"
}
Write-Host ""
Write-Host "日志目录: $LogDir（api.log / worker.log / frontend.log）" -ForegroundColor DarkGray
Write-Host "注意: 国际源爬虫需 HTTPS_PROXY=http://127.0.0.1:7890；队列有遗留 crawl 任务会被 worker 立即消费" -ForegroundColor DarkGray
