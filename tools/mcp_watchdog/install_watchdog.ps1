# Registers the MCP watchdog as a Scheduled Task that runs every 5 minutes (elevated, at logon).
# It restarts wedged MCP servers, which requires killing processes + starting other tasks -> RunLevel
# Highest. Registering a Scheduled Task itself requires Administrator.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) { Write-Host "[X] Run this elevated (Administrator)." -ForegroundColor Red; exit 1 }

$script = Join-Path $here "mcp_watchdog.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`"" -WorkingDirectory $here

# Repeat every 5 minutes, indefinitely. TWO triggers:
#   1. AtLogOn + repetition -- cadence from every logon.
#   2. Once (now) + repetition -- cadence starts immediately after (re)install, and survives the
#      sleep/wake edge where an AtLogOn repetition silently stops ticking until the next logon
#      (observed 2026-07-16: repetition died mid-day and the watchdog never ran again).
$rep = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
  -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)).Repetition
$trigLogon = New-ScheduledTaskTrigger -AtLogOn
$trigLogon.Repetition = $rep
$trigNow = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
  -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
$trigger = @($trigLogon, $trigNow)

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -RunLevel Highest -LogonType Interactive
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 4) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "MCP-Watchdog" -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "[OK] Registered 'MCP-Watchdog' (elevated, every 5 min). Log: $(Join-Path $here 'watchdog.log')" -ForegroundColor Green
Write-Host "     Run now:  Start-ScheduledTask -TaskName MCP-Watchdog" -ForegroundColor Cyan
Write-Host "     Dry-run:  powershell -ExecutionPolicy Bypass -File .\mcp_watchdog.ps1 -DryRun" -ForegroundColor Cyan
Write-Host "     Remove:   .\uninstall_watchdog.ps1" -ForegroundColor Cyan
