# Removes the Disk-MCP scheduled task. Run as Administrator.
Unregister-ScheduledTask -TaskName "Disk-MCP" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "[OK] Removed scheduled task 'Disk-MCP' (if it existed)." -ForegroundColor Green
