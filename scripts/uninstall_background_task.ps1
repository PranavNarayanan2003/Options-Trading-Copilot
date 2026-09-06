$TaskName = "AI Options Trading Copilot"
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
Write-Host "Removed scheduled task: $TaskName" -ForegroundColor Green
