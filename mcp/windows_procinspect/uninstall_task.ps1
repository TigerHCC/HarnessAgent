# Removes the mcp-procinspect scheduled task. Run as Administrator.
Unregister-ScheduledTask -TaskName "mcp-procinspect" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "[OK] Removed scheduled task 'mcp-procinspect' (if it existed)." -ForegroundColor Green
