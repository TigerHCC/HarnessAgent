# Registers a Scheduled Task that runs the DTM SDK MCP server elevated at logon. Run as Administrator.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) { Write-Host "[X] Run this elevated (Administrator)." -ForegroundColor Red; exit 1 }

$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { Write-Host "[X] Python 3 not found on PATH." -ForegroundColor Red; exit 1 }
$server = Join-Path $here "dtm_sdk_mcp_server.py"
$action = New-ScheduledTaskAction -Execute $py -Argument "`"$server`"" -WorkingDirectory $here
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -RunLevel Highest -LogonType Interactive
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "DtmSdk-MCP" -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "[OK] Registered scheduled task 'DtmSdk-MCP' (elevated, at logon)." -ForegroundColor Green
Write-Host "     Start now: Start-ScheduledTask -TaskName DtmSdk-MCP" -ForegroundColor Cyan
Write-Host "     Remove:    .\uninstall_task.ps1" -ForegroundColor Cyan
