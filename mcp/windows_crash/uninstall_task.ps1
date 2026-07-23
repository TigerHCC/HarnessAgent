# Removes the mcp-crash scheduled task. Run as Administrator.
Unregister-ScheduledTask -TaskName "mcp-crash" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "[OK] Removed scheduled task 'mcp-crash' (if it existed)." -ForegroundColor Green
