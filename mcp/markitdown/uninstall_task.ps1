# Removes the MarkItDown MCP scheduled task.
$ErrorActionPreference = "SilentlyContinue"
Unregister-ScheduledTask -TaskName "MarkItDown-MCP" -Confirm:$false
Write-Host "[OK] Removed scheduled task 'MarkItDown-MCP' (if it existed)." -ForegroundColor Green
