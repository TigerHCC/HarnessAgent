# Removes the Scheduler MCP scheduled task.
$ErrorActionPreference = "SilentlyContinue"
Unregister-ScheduledTask -TaskName "Scheduler-MCP" -Confirm:$false
Write-Host "[OK] Removed scheduled task 'Scheduler-MCP' (if it existed)." -ForegroundColor Green
