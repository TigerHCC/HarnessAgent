# Removes the MarkItDown MCP scheduled task.
$ErrorActionPreference = "SilentlyContinue"
Unregister-ScheduledTask -TaskName "mcp-markitdown" -Confirm:$false
Write-Host "[OK] Removed scheduled task 'mcp-markitdown' (if it existed)." -ForegroundColor Green
