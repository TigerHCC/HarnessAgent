# Removes the mcp-eventlog scheduled task. Run as Administrator.
Unregister-ScheduledTask -TaskName "mcp-eventlog" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "[OK] Removed scheduled task 'mcp-eventlog' (if it existed)." -ForegroundColor Green
