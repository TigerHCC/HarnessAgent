# Registers a Scheduled Task that runs the Process-Inspection MCP server elevated at logon. Run as Administrator.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) { Write-Host "[X] Run this elevated (Administrator)." -ForegroundColor Red; exit 1 }

$py = (Get-Command python).Source
$server = Join-Path $here "procinspect_mcp_server.py"
$action = New-ScheduledTaskAction -Execute $py -Argument "`"$server`"" -WorkingDirectory $here
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -RunLevel Highest -LogonType Interactive
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "Procinspect-MCP" -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "[OK] Registered scheduled task 'Procinspect-MCP' (elevated, at logon)." -ForegroundColor Green
Write-Host "     Start now: Start-ScheduledTask -TaskName Procinspect-MCP" -ForegroundColor Cyan
Write-Host "     Remove:    .\uninstall_task.ps1" -ForegroundColor Cyan
