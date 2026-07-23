# Removes the DTM Download MCP scheduled task.
$ErrorActionPreference = "SilentlyContinue"
Unregister-ScheduledTask -TaskName "mcp-dtm_download" -Confirm:$false
Write-Host "[OK] Removed scheduled task 'mcp-dtm_download' (if it existed)." -ForegroundColor Green
