# Removes the Procinspect-MCP scheduled task. Run as Administrator.
Unregister-ScheduledTask -TaskName "Procinspect-MCP" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "[OK] Removed scheduled task 'Procinspect-MCP' (if it existed)." -ForegroundColor Green
