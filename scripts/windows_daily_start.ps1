# Starts the paper-trading dashboard once per day, skipping Saturdays,
# Sundays, and NSE trading holidays (read from local-bot.env's NSE_HOLIDAYS).
# Meant to be run by a Windows Scheduled Task shortly before market open.
# Never starts live trading -- this only ever launches the paper-mode web
# dashboard (LIVE_TRADING_ENABLED stays false in local-bot.env).

$ErrorActionPreference = "Stop"

$RepoDir = "D:\Claude DND"
$ConfigFile = Join-Path $RepoDir "local-bot.env"
$PasswordFile = Join-Path $RepoDir ".termux-data\web-password"
$PythonExe = "C:\PyDND312-embed\python.exe"
$LogFile = Join-Path $RepoDir "logs\dashboard-daily.log"
$Port = 8000

$today = Get-Date
$dayOfWeek = $today.DayOfWeek

if ($dayOfWeek -eq "Saturday" -or $dayOfWeek -eq "Sunday") {
    Write-Output "$(Get-Date -Format o): Skipping -- $dayOfWeek is a weekend."
    exit 0
}

# Parse NSE_HOLIDAYS=YYYY-MM-DD,YYYY-MM-DD,... out of local-bot.env without
# touching any other config parsing -- this script only needs that one value.
$holidayLine = Select-String -Path $ConfigFile -Pattern "^NSE_HOLIDAYS=" | Select-Object -First 1
$holidays = @()
if ($holidayLine) {
    $value = ($holidayLine.Line -replace "^NSE_HOLIDAYS=", "").Trim()
    if ($value.Length -gt 0) {
        $holidays = $value -split "," | ForEach-Object { $_.Trim() }
    }
}
$todayStr = $today.ToString("yyyy-MM-dd")
if ($holidays -contains $todayStr) {
    Write-Output "$(Get-Date -Format o): Skipping -- $todayStr is an NSE trading holiday."
    exit 0
}

# Avoid a duplicate launch if the dashboard is already up (e.g. a manual
# start earlier the same day, or the task firing twice).
try {
    $response = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
    Write-Output "$(Get-Date -Format o): Skipping -- dashboard already responding on port $Port."
    exit 0
} catch {
    if ($_.Exception.Response -and $_.Exception.Response.StatusCode.value__ -eq 401) {
        Write-Output "$(Get-Date -Format o): Skipping -- dashboard already responding on port $Port (401 = alive, needs auth)."
        exit 0
    }
    # Any other error (connection refused, timeout) means nothing is listening -- proceed to start it.
}

if (-not (Test-Path $PasswordFile)) {
    throw "Web password file not found at $PasswordFile -- dashboard was never started manually to generate it."
}
$env:OPTIONS_BOT_WEB_PASSWORD = (Get-Content $PasswordFile -Raw).Trim()

New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null

Write-Output "$(Get-Date -Format o): Starting dashboard for trading day $todayStr..."
# Start-Process -ArgumentList joins its array into one command-line string
# without quoting individual elements that contain spaces -- unlike calling
# an exe directly with &. $ConfigFile ("D:\Claude DND\local-bot.env") has a
# space in it, so it must carry its own embedded quote characters here or it
# silently splits into two arguments (caught 2026-08-26: the scheduled task
# ran and reported success every day, but argparse received the second half
# of the path as its positional "command" argument and the dashboard never
# actually started -- see logs/dashboard-daily.log.err).
Start-Process -FilePath $PythonExe `
    -ArgumentList "-u", "-m", "options_bot.cli", "--config", "`"$ConfigFile`"", "web", "--host", "127.0.0.1", "--port", "$Port" `
    -WorkingDirectory $RepoDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $LogFile `
    -RedirectStandardError "$LogFile.err"
Write-Output "$(Get-Date -Format o): Launch requested."
