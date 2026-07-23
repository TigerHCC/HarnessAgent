# Removes the Scheduler MCP scheduled task.
$ErrorActionPreference = "SilentlyContinue"
Unregister-ScheduledTask -TaskName "mcp-scheduler" -Confirm:$false
Write-Host "[OK] Removed scheduled task 'mcp-scheduler' (if it existed)." -ForegroundColor Green
