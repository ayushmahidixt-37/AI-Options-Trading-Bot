# Hard-stops the paper-trading dashboard at end of day. Positions are
# already force-exited by the application itself at FORCE_EXIT_IST (15:20)
# and the market closes at 15:30 -- this just stops the process afterward
# so nothing is left running unattended overnight. Meant to be run by a
# Windows Scheduled Task around 16:30 IST on trading days.

$ErrorActionPreference = "Stop"
$Port = 8000

$processes = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "python.exe" -and $_.CommandLine -like "*options_bot.cli*web*"
}

if (-not $processes) {
    Write-Output "$(Get-Date -Format o): Nothing to stop -- dashboard is not running."
    exit 0
}

foreach ($proc in $processes) {
    Write-Output "$(Get-Date -Format o): Stopping dashboard process $($proc.ProcessId)..."
    Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
}
Write-Output "$(Get-Date -Format o): Stop complete."
