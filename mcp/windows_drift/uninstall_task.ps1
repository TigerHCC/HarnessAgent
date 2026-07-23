# Removes the mcp-drift scheduled task. Run as Administrator.
Unregister-ScheduledTask -TaskName "mcp-drift" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "[OK] Removed scheduled task 'mcp-drift' (if it existed)." -ForegroundColor Green
