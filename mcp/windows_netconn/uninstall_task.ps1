# Removes the mcp-netconn scheduled task. Run as Administrator.
Unregister-ScheduledTask -TaskName "mcp-netconn" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "[OK] Removed scheduled task 'mcp-netconn' (if it existed)." -ForegroundColor Green
