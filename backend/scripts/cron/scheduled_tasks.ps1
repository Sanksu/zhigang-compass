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
#        Start-Process -WindowStyle Hidden -FilePath "uv" -ArgumentList "run","arq","app.workers.settings.WorkerSettings"
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
# 采集责任划分：
#   - boss/zhilian/indeed/glassdoor/arxiv/github/stackoverflow 由 ETL 主管线
#     （05:00）阶段 1 统一采集，不再单独调度（避免同一平台每日重复采集）；
#   - maimai 为夜间合规窗口（23:00 - 06:00，≤100 req/h），保持独立调度；
#   - linkedin_public / 课程平台不在 ETL 采集列表内，保持独立调度；
#   - 新岗位发现 + 自动流转已链入 ETL 阶段 15（快照发布之后），无需独立任务。
$Tasks = @(
    # 脉脉夜间合规窗口（23:00）
    @{ Name = "CrawlMaimai";    Time = "23:00"; Script = "crawl_spider.py"; Args = @("maimai", "30") },
    # 国际非招聘源（北京时间 08:00 = UTC 0:00）
    @{ Name = "CrawlLinkedIn";  Time = "08:00"; Script = "crawl_spider.py"; Args = @("linkedin_public", "50"); Proxy = $true },
    # 课程平台每周日全量同步（北京时间 10:00 = UTC 2:00，对齐 crontab.example）
    @{ Name = "CrawlCoursera";   Time = "10:00"; DaysOfWeek = "Sunday"; Script = "crawl_spider.py"; Args = @("coursera", "100"); Proxy = $true },
    @{ Name = "CrawlEdx";        Time = "10:30"; DaysOfWeek = "Sunday"; Script = "crawl_spider.py"; Args = @("edx", "100"); Proxy = $true },
    @{ Name = "CrawlIcourse163"; Time = "11:00"; DaysOfWeek = "Sunday"; Script = "crawl_spider.py"; Args = @("icourse163", "100") },
    # ETL 主管线（05:00；阶段 1 采集 + LLM 抽取 + 快照 + 发现/自动流转）
    @{ Name = "ETLDaily";       Time = "05:00"; Script = "etl_daily.py";    Args = @() },
    # 图谱健康治理（06:30，ETL 完成后；脏边/伪技能自动清理，备份 reports/graph_health_*）
    @{ Name = "GraphHealth";    Time = "06:30"; Script = "graph_health_daily.py"; Args = @() }
    # 岗位重复对治理（06:45，GraphHealth 之后；变体合并/语义提议，备份 reports/position_duplicates_*）
    @{ Name = "PositionDup";    Time = "06:45"; Script = "position_dup_daily.py"; Args = @() }
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
    # 固定日志名（08-15 修复）：Get-Date 在注册时只求值一次，任务命令里的日期
    # 会永远写死为注册当天——今日日志追加进旧文件难排查；固定名 + >> 追加
    # 天然保留全部历史，无日期混淆
    $logFile = Join-Path $LogDir "$($task.Name).log"

    # 构造命令（cmd 语法：路径无空格前提下不用引号；单引号为 PowerShell 语法
    # cmd 不识别——2026-08-13 实测 05:00 ETLDaily 退出码 1 根因之一）
    $argString = ($task.Args | ForEach-Object { $_ }) -join ' '
    $cmd = "cd /d $BackendDir && uv run python scripts\cron\$($task.Script) $argString >> $logFile 2>&1"

    if ($task.Proxy) {
        $cmd = "set HTTPS_PROXY=http://127.0.0.1:7890 && $cmd"
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

    # 用 cmd /c 包装：PS5.1 将 native 命令 stderr（uv warning 等）误判为
    # NativeCommandError 导致退出码 1（2026-08-13 ETLDaily 实测）；cmd 的
    # 2>&1 重定向不产生该错误，任务退出码 = 实际命令退出码
    $action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$cmd`""
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
Write-Host "  Start-Process -WindowStyle Hidden -FilePath 'uv' -ArgumentList 'run','arq','app.workers.settings.WorkerSettings'"
