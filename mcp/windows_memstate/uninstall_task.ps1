# Removes the Memstate-MCP scheduled task. Run as Administrator.
Unregister-ScheduledTask -TaskName "Memstate-MCP" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "[OK] Removed scheduled task 'Memstate-MCP' (if it existed)." -ForegroundColor Green
