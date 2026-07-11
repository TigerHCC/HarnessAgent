# Removes the Drift-MCP scheduled task. Run as Administrator.
Unregister-ScheduledTask -TaskName "Drift-MCP" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "[OK] Removed scheduled task 'Drift-MCP' (if it existed)." -ForegroundColor Green
