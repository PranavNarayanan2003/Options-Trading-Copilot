$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$StartScript = Join-Path $Root "scripts\start_live.ps1"
$TaskName = "AI Options Trading Copilot"

if (-not (Test-Path $StartScript)) {
    throw "Could not find $StartScript"
}

# 20:45 Singapore is deliberately early enough for both US daylight-saving
# regimes: about 08:45 ET during EDT and 07:45 ET during EST.
$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$StartScript`""

$Trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At 8:45PM

$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 5)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Runs the AI Options Trading Copilot on weekday US-market mornings. The app itself permits new alerts only from 09:30-11:30 ET." `
    -Force | Out-Null

Write-Host "Installed scheduled task: $TaskName" -ForegroundColor Green
Write-Host "Weekdays at 20:45 Singapore time; maximum runtime 5 hours." 
Write-Host "Start it now with: Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "View status with: Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
Write-Host "Logs: $Root\logs\live-session.log"
