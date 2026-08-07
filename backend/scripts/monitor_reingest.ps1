# 日志监控：监听重抽（after_backfill_reingest）进度，完成或出错时桌面通知。
#
# 判定口径：
# - 完成：reingest_auto.log 出现 "[after_backfill_reingest] 全部完成"
# - 错误：reingest_auto.err.log 非空（traceback），或进程已退出但日志无完成标记
#
# 通知：提示音 + 系统气泡（NotifyIcon），事件写 logs/notify.log（可审计）。
#
# 用法（脱离终端后台运行，避免 RunCommand 终端复用误杀）：
#   Start-Process pwsh -ArgumentList '-NoProfile','-File','scripts/monitor_reingest.ps1' -WindowStyle Hidden

param(
    [int]$PollSeconds = 60
)

$ErrorActionPreference = 'Stop'
$Backend = Split-Path -Parent $PSScriptRoot
$Log = Join-Path $Backend 'logs\reingest_auto.log'
$ErrLog = Join-Path $Backend 'logs\reingest_auto.err.log'
$NotifyLog = Join-Path $Backend 'logs\notify.log'
$CompletionMark = '[after_backfill_reingest] 全部完成'

# 幂等：命名互斥量（进程内持锁，重复启动立即退出；命令行匹配会误伤启动命令本身）
$mutex = New-Object System.Threading.Mutex($false, 'ZhigangCompass_MonitorReingest')
if (-not $mutex.WaitOne(0, $false)) {
    Write-Host 'monitor_reingest 已在运行，退出'
    exit 0
}

function Write-Event([string]$level, [string]$msg) {
    $line = "{0} [{1}] {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $level, $msg
    Add-Content -Path $NotifyLog -Value $line -Encoding UTF8
    Write-Host $line
}

function Send-Notify([string]$title, [string]$text) {
    # 提示音（SystemSounds 走声卡，比 console beep 可靠）
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    [System.Media.SystemSounds]::Exclamation.Play()
    Start-Sleep -Milliseconds 300
    [System.Media.SystemSounds]::Exclamation.Play()
    # 气泡通知：保持 NotifyIcon 存活足够时长让气泡完整显示
    $tip = New-Object System.Windows.Forms.NotifyIcon
    $tip.Icon = [System.Drawing.SystemIcons]::Information
    $tip.Visible = $true
    $tip.BalloonTipTitle = $title
    $tip.BalloonTipText = $text
    $tip.BalloonTipIcon = 'Info'
    $tip.ShowBalloonTip(10000)
    Start-Sleep -Seconds 12
    $tip.Dispose()
}

function Test-ReingestAlive {
    return [bool](Get-CimInstance Win32_Process |
        Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -match 'after_backfill_reingest' })
}

Write-Event 'info' "监控启动：$Log（轮询 ${PollSeconds}s）"

while ($true) {
    Start-Sleep -Seconds $PollSeconds

    # 1) 完成（Contains 字面匹配：标记含 [] 会被 -match 正则误解析为字符类）
    try {
        $text = if (Test-Path $Log) { Get-Content $Log -Raw -ErrorAction SilentlyContinue } else { '' }
    } catch { $text = '' }
    if ($text.Contains($CompletionMark)) {
        $last = ((Get-Content $Log | Select-Object -Last 1) -join ' ')
        $summary = if ($last.Length -gt 120) { $last.Substring(0, 120) } else { $last }
        Write-Event 'done' $last
        Send-Notify '重抽完成' "全库 LLM 抽取与岗位重聚合已完成。`n$summary"
        exit 0
    }

    # 2) 错误：Python 崩溃 traceback。
    # stderr 常含 HF 加载 / Neo4j GqlStatus 告警噪音（非致命），不可按"err 非空"判错。
    try {
        $err = if (Test-Path $ErrLog) { Get-Content $ErrLog -Raw -ErrorAction SilentlyContinue } else { '' }
    } catch { $err = '' }
    if ($err -match 'Traceback \(most recent call last\)') {
        $idx = $err.IndexOf('Traceback (most recent call last)')
        Write-Event 'error' "Python 崩溃：$($err.Substring($idx, [Math]::Min(400, $err.Length - $idx)))"
        Send-Notify '重抽出错' '重抽进程崩溃（见 traceback），详见 logs/reingest_auto.err.log'
        exit 1
    }

    # 3) 错误：进程已退出但无完成标记
    if (-not (Test-ReingestAlive)) {
        Write-Event 'error' '重抽进程已退出，但日志无完成标记'
        Send-Notify '重抽中断' '重抽进程已退出且未见完成标记，请检查 logs/notify.log'
        exit 1
    }
}
