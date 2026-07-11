# Removes the Exec-MCP scheduled task. Run as Administrator.
Unregister-ScheduledTask -TaskName "Exec-MCP" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "[OK] Removed scheduled task 'Exec-MCP' (if it existed)." -ForegroundColor Green
