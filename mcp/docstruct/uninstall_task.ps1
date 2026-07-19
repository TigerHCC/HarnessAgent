# Removes the DocStruct MCP scheduled task.
$ErrorActionPreference = "SilentlyContinue"
Unregister-ScheduledTask -TaskName "DocStruct-MCP" -Confirm:$false
Write-Host "[OK] Removed scheduled task 'DocStruct-MCP' (if it existed)." -ForegroundColor Green
