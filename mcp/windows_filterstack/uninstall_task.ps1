# Removes the Filterstack-MCP scheduled task. Run as Administrator.
Unregister-ScheduledTask -TaskName "Filterstack-MCP" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "[OK] Removed scheduled task 'Filterstack-MCP' (if it existed)." -ForegroundColor Green
