# Removes the Netconn-MCP scheduled task. Run as Administrator.
Unregister-ScheduledTask -TaskName "Netconn-MCP" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "[OK] Removed scheduled task 'Netconn-MCP' (if it existed)." -ForegroundColor Green
