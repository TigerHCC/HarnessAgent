# Removes the SRUM-MCP scheduled task. Run as Administrator.
Unregister-ScheduledTask -TaskName "SRUM-MCP" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "[OK] Removed scheduled task 'SRUM-MCP' (if it existed)." -ForegroundColor Green
