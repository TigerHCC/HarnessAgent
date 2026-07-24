<#
Registers the Alienware-specific GooseWeb logon task.
The shared config.json remains untouched; this task selects
config.WINDOWS_ALIENWARE.json through GOOSE_WEB_CONFIG.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot
$repoRoot = Split-Path -Parent $here
$serve = Join-Path $here 'serve_web.ps1'
$webConfig = Join-Path $here 'config.WINDOWS_ALIENWARE.json'
$logDir = Join-Path $repoRoot 'logs'
$log = Join-Path $logDir 'goose_web.log'

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$principalCheck = New-Object Security.Principal.WindowsPrincipal($id)
if (-not $principalCheck.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
  throw 'Run this script from an elevated Administrator PowerShell.'
}
if (-not (Test-Path -LiteralPath $serve)) { throw "Missing $serve" }
if (-not (Test-Path -LiteralPath $webConfig)) { throw "Missing $webConfig" }

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$powershell = (Get-Command powershell.exe).Source
$command = "`$env:GOOSE_WEB_CONFIG='$webConfig'; & '$serve' *> '$log'"
$action = New-ScheduledTaskAction -Execute $powershell `
  -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command `"$command`"" `
  -WorkingDirectory $here
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
  -RunLevel Highest -LogonType Interactive
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName 'GooseWeb' -Action $action -Trigger $trigger `
  -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName 'GooseWeb'
Write-Host "[OK] GooseWeb uses $webConfig" -ForegroundColor Green
