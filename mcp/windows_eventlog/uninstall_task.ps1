# Removes the EventLog-MCP scheduled task. Run as Administrator.
Unregister-ScheduledTask -TaskName "EventLog-MCP" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "[OK] Removed scheduled task 'EventLog-MCP' (if it existed)." -ForegroundColor Green
