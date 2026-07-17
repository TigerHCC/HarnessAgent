# Removes the DTM Deploy MCP scheduled task.
$ErrorActionPreference = "SilentlyContinue"
Unregister-ScheduledTask -TaskName "DtmDeploy-MCP" -Confirm:$false
Write-Host "[OK] Removed scheduled task 'DtmDeploy-MCP' (if it existed)." -ForegroundColor Green
