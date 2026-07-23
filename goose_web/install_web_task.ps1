# Registers goose_web as a Scheduled Task 'GooseWeb' -- runs serve_web.ps1 at logon, ELEVATED
# (RunLevel Highest, needed to bind ALL interfaces on :8799 for LAN/Meshnet access without a urlacl).
# All output is redirected to logs\goose_web.log. ExecutionTimeLimit is unlimited (a long-running
# server). Registering a Scheduled Task itself requires Administrator.
#
# Manage after install:
#   Start-ScheduledTask -TaskName GooseWeb
#   Stop-ScheduledTask  -TaskName GooseWeb ; Start-ScheduledTask -TaskName GooseWeb   # restart
#   .\uninstall_web_task.ps1                                                          # remove
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $here
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) { Write-Host "[X] Run this elevated (Administrator) to register the task." -ForegroundColor Red; exit 1 }

$powershell = (Get-Command powershell).Source
$serve = Join-Path $here "serve_web.ps1"
if (-not (Test-Path -LiteralPath $serve)) { Write-Host "[X] serve_web.ps1 not found at $serve" -ForegroundColor Red; exit 1 }
$logDir = Join-Path $repoRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "goose_web.log"

# stop any manually-running goose_web so the task's instance can bind :8799
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
  Where-Object { $_.CommandLine -match 'serve_web\.ps1|server\.ps1' -and $_.ProcessId -ne $PID } |
  ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force } catch {} }
Start-Sleep -Seconds 2

# -Command "& '<serve>' *> '<log>'"  -- run the server and tee all streams to the log
$arg = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command ""& '$serve' *> '$log'"""
$action    = New-ScheduledTaskAction -Execute $powershell -Argument $arg -WorkingDirectory $here
$trigger   = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -RunLevel Highest -LogonType Interactive
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "GooseWeb" -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "[OK] Registered scheduled task 'GooseWeb' (elevated, at logon; binds :8799 on all interfaces)." -ForegroundColor Green
Write-Host "     Start now: Start-ScheduledTask -TaskName GooseWeb" -ForegroundColor Cyan
Write-Host "     Restart:   Stop-ScheduledTask -TaskName GooseWeb; Start-ScheduledTask -TaskName GooseWeb" -ForegroundColor Cyan
Write-Host "     Log:       $log" -ForegroundColor Cyan
Write-Host "     Remove:    .\uninstall_web_task.ps1" -ForegroundColor Cyan
