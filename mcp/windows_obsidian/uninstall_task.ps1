# Removes the Obsidian MCP scheduled task.
$ErrorActionPreference = "SilentlyContinue"
Unregister-ScheduledTask -TaskName "mcp-obsidian" -Confirm:$false
Write-Host "[OK] Removed scheduled task 'mcp-obsidian' (if it existed)." -ForegroundColor Green
