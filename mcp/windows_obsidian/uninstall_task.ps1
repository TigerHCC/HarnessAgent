# Removes the Obsidian MCP scheduled task.
$ErrorActionPreference = "SilentlyContinue"
Unregister-ScheduledTask -TaskName "Obsidian-MCP" -Confirm:$false
Write-Host "[OK] Removed scheduled task 'Obsidian-MCP' (if it existed)." -ForegroundColor Green
