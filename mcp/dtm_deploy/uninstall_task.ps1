# Removes the DTM Deploy MCP scheduled task.
$ErrorActionPreference = "SilentlyContinue"
Unregister-ScheduledTask -TaskName "mcp-dtm_deploy" -Confirm:$false
Write-Host "[OK] Removed scheduled task 'mcp-dtm_deploy' (if it existed)." -ForegroundColor Green
