# Removes the Winupdate-MCP scheduled task. Run as Administrator.
Unregister-ScheduledTask -TaskName "Winupdate-MCP" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "[OK] Removed scheduled task 'Winupdate-MCP' (if it existed)." -ForegroundColor Green
