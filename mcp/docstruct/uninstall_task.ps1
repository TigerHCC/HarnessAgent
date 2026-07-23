# Removes the DocStruct MCP scheduled task.
$ErrorActionPreference = "SilentlyContinue"
Unregister-ScheduledTask -TaskName "mcp-docstruct" -Confirm:$false
Write-Host "[OK] Removed scheduled task 'mcp-docstruct' (if it existed)." -ForegroundColor Green
