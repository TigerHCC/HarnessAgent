# Removes the mcp-exec scheduled task. Run as Administrator.
Unregister-ScheduledTask -TaskName "mcp-exec" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "[OK] Removed scheduled task 'mcp-exec' (if it existed)." -ForegroundColor Green
