# Removes the mcp-srum scheduled task. Run as Administrator.
Unregister-ScheduledTask -TaskName "mcp-srum" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "[OK] Removed scheduled task 'mcp-srum' (if it existed)." -ForegroundColor Green
