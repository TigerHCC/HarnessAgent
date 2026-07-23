# Removes the mcp-winupdate scheduled task. Run as Administrator.
Unregister-ScheduledTask -TaskName "mcp-winupdate" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "[OK] Removed scheduled task 'mcp-winupdate' (if it existed)." -ForegroundColor Green
