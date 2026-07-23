# Removes the mcp-memstate scheduled task. Run as Administrator.
Unregister-ScheduledTask -TaskName "mcp-memstate" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "[OK] Removed scheduled task 'mcp-memstate' (if it existed)." -ForegroundColor Green
