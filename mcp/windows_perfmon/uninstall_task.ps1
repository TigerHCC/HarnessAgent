# Removes the Perfmon-MCP scheduled task. Run as Administrator.
Unregister-ScheduledTask -TaskName "Perfmon-MCP" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "[OK] Removed scheduled task 'Perfmon-MCP' (if it existed)." -ForegroundColor Green
