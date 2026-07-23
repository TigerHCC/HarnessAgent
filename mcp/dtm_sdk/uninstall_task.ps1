# Removes the DTM SDK MCP scheduled task.
$ErrorActionPreference = "SilentlyContinue"
Unregister-ScheduledTask -TaskName "mcp-dtmsdk" -Confirm:$false
Write-Host "[OK] Removed scheduled task 'mcp-dtmsdk' (if it existed)." -ForegroundColor Green
