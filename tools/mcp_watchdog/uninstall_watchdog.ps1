# Removes the MCP watchdog scheduled task.
$ErrorActionPreference = "SilentlyContinue"
Unregister-ScheduledTask -TaskName "MCP-Watchdog" -Confirm:$false
Write-Host "[OK] Removed scheduled task 'MCP-Watchdog' (if it existed)." -ForegroundColor Green
