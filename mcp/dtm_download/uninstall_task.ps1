# Removes the DTM Download MCP scheduled task.
$ErrorActionPreference = "SilentlyContinue"
Unregister-ScheduledTask -TaskName "DtmDownload-MCP" -Confirm:$false
Write-Host "[OK] Removed scheduled task 'DtmDownload-MCP' (if it existed)." -ForegroundColor Green
