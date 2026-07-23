# Removes the mcp-disk scheduled task. Run as Administrator.
Unregister-ScheduledTask -TaskName "mcp-disk" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "[OK] Removed scheduled task 'mcp-disk' (if it existed)." -ForegroundColor Green
