# 智岗罗盘 ETL 调度 Windows 计划任务示例（开发环境）
#
# 部署（管理员 PowerShell）：
#   cd backend
#   .\scripts\cron\scheduled_tasks.ps1
#
# 卸载：
#   .\scripts\cron\scheduled_tasks.ps1 -Uninstall
#
# 前置条件：
#   1. Redis 已启动（默认 redis://localhost:6379/1）
#   2. ARQ Worker 已运行：
#        Start-Process -WindowStyle Hidden -FilePath "uv" -ArgumentList "run","arq","app.workers.tasks.WorkerSettings"
#   3. 国际源需配置 HTTPS_PROXY 环境变量（Clash/V2Ray Rule 模式）
#
# 注：Windows 计划任务时间按服务器本地时间（开发机建议设为 Asia/Shanghai）

param(
    [switch]$Uninstall = $false,
    [string]$BackendDir = (Get-Location).Path,
    [string]$LogDir = (Join-Path (Get-Location).Path "..\logs")
)

# 任务名前缀，便于批量卸载
$TaskPrefix = "ZhigangETL_"

# 调度任务定义（对齐设计文档 §4.4）
$Tasks = @(
    # 国内 A 级招聘平台（02:00 / 02:15）
    @{ Name = "CrawlBoss";     Time = "02:00"; Script = "crawl_spider.py"; Args = @("boss", "100") },
    @{ Name = "CrawlZhilian";  Time = "02:15"; Script = "crawl_spider.py"; Args = @("zhilian", "100") },
    # 国际 A/B 级招聘平台（04:00 错峰）。monster 已从自动采集移除（DataDome
    # 防护在容器环境不可绕过，见 tasks.py run_etl_pipeline 注释）
    @{ Name = "CrawlIndeed";    Time = "04:20"; Script = "crawl_spider.py"; Args = @("indeed", "50");   Proxy = $true },
    @{ Name = "CrawlGlassdoor"; Time = "04:40"; Script = "crawl_spider.py"; Args = @("glassdoor", "50"); Proxy = $true },
    # 脉脉夜间合规窗口（23:00）
    @{ Name = "CrawlMaimai";    Time = "23:00"; Script = "crawl_spider.py"; Args = @("maimai", "30") },
    # 国际非招聘源（北京时间 08:00 = UTC 0:00）
    @{ Name = "CrawlLinkedIn";  Time = "08:00"; Script = "crawl_spider.py"; Args = @("linkedin_public", "50"); Proxy = $true },
    @{ Name = "CrawlGithub";    Time = "08:15"; Script = "crawl_spider.py"; Args = @("github", "50");   Proxy = $true },
    @{ Name = "CrawlSO";        Time = "08:30"; Script = "crawl_spider.py"; Args = @("stackoverflow", "50"); Proxy = $true },
    # arXiv（北京时间 11:00 = UTC 3:00）
    @{ Name = "CrawlArxiv";     Time = "11:00"; Script = "crawl_spider.py"; Args = @("arxiv", "50");   Proxy = $true },
    # 课程平台每周日全量同步（北京时间 10:00 = UTC 2:00，对齐 crontab.example）
    @{ Name = "CrawlCoursera";   Time = "10:00"; DaysOfWeek = "Sunday"; Script = "crawl_spider.py"; Args = @("coursera", "100"); Proxy = $true },
    @{ Name = "CrawlEdx";        Time = "10:30"; DaysOfWeek = "Sunday"; Script = "crawl_spider.py"; Args = @("edx", "100"); Proxy = $true },
    @{ Name = "CrawlIcourse163"; Time = "11:00"; DaysOfWeek = "Sunday"; Script = "crawl_spider.py"; Args = @("icourse163", "100") },
    # ETL 主管线（05:00）
    @{ Name = "ETLDaily";       Time = "05:00"; Script = "etl_daily.py";    Args = @() },
    # 新岗位发现 + 自动状态流转（05:30，ETL 阶段 12 快照发布后）
    @{ Name = "DiscoveryDaily"; Time = "05:30"; Script = "discovery_daily.py"; Args = @() }
)

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

if ($Uninstall) {
    Write-Host "卸载 ZhigangETL 计划任务..." -ForegroundColor Yellow
    Get-ScheduledTask | Where-Object { $_.TaskName -like "$TaskPrefix*" } | ForEach-Object {
        Unregister-ScheduledTask -TaskName $_.TaskName -Confirm:$false
        Write-Host "  已移除: $($_.TaskName)" -ForegroundColor Green
    }
    exit 0
}

# 安装任务
foreach ($task in $Tasks) {
    $taskName = "$TaskPrefix$($task.Name)"
    $scriptPath = Join-Path $BackendDir "scripts\cron\$($task.Script)"
    $logFile = Join-Path $LogDir "$($task.Name)_$(Get-Date -Format 'yyyyMMdd').log"

    # 构造命令
    $argString = ($task.Args | ForEach-Object { '"' + $_ + '"' }) -join ' '
    $cmd = "cd '$BackendDir'; uv run python '$scriptPath' $argString >> '$logFile' 2>&1"

    if ($task.Proxy) {
        $cmd = '$env:HTTPS_PROXY="http://127.0.0.1:7890"; ' + $cmd
    }

    # 解析时间（HH:mm）；指定 DaysOfWeek 的任务为每周触发（课程平台），否则每日
    $timeParts = $task.Time.Split(':')
    $at = "$($timeParts[0]):$($timeParts[1])"
    if ($task.DaysOfWeek) {
        $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $task.DaysOfWeek -At $at
    } else {
        $trigger = New-ScheduledTaskTrigger -Daily -At $at
    }

    # 以当前用户登录时运行
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -Command `"$cmd`""
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

    # 已存在则先注销
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    }

    Register-ScheduledTask -TaskName $taskName -Trigger $trigger -Principal $principal -Action $action -Settings $settings -Force | Out-Null
    Write-Host "  已注册: $taskName @ $($task.Time)" -ForegroundColor Green
}

Write-Host ""
Write-Host "已注册 $($Tasks.Count) 个计划任务，前缀: $TaskPrefix" -ForegroundColor Cyan
Write-Host "日志目录: $LogDir" -ForegroundColor Cyan
Write-Host ""
Write-Host "提示：ARQ Worker 需独立运行：" -ForegroundColor Yellow
Write-Host "  Start-Process -WindowStyle Hidden -FilePath 'uv' -ArgumentList 'run','arq','app.workers.tasks.WorkerSettings'"
