# Unregisters the scheduled task for the Windows Audio MCP server. Run as Administrator.
$ErrorActionPreference = "Stop"
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) { Write-Host "[X] Run this elevated (Administrator)." -ForegroundColor Red; exit 1 }
Unregister-ScheduledTask -TaskName "mcp-audio" -Confirm:$false | Out-Null
Write-Host "[OK] Unregistered scheduled task 'mcp-audio'." -ForegroundColor Green
