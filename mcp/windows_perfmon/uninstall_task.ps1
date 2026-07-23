# Removes the mcp-perfmon scheduled task. Run as Administrator.
Unregister-ScheduledTask -TaskName "mcp-perfmon" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "[OK] Removed scheduled task 'mcp-perfmon' (if it existed)." -ForegroundColor Green
