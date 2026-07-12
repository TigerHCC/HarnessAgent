# Removes the DTM SDK MCP scheduled task.
$ErrorActionPreference = "SilentlyContinue"
Unregister-ScheduledTask -TaskName "DtmSdk-MCP" -Confirm:$false
Write-Host "[OK] Removed scheduled task 'DtmSdk-MCP' (if it existed)." -ForegroundColor Green
