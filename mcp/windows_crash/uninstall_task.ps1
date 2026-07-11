# Removes the Crash-MCP scheduled task. Run as Administrator.
Unregister-ScheduledTask -TaskName "Crash-MCP" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "[OK] Removed scheduled task 'Crash-MCP' (if it existed)." -ForegroundColor Green
