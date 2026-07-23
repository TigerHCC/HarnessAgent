# Registers a Scheduled Task that runs the docstruct MCP server at logon, UNELEVATED
# (RunLevel Limited). Registering a Scheduled Task itself requires Administrator (a Windows requirement).
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent (Split-Path -Parent $here)
$launcher = Join-Path $repoRoot "scripts\start_mcp_hidden.ps1"
. (Join-Path $repoRoot "scripts\mcp_task_helpers.ps1")
$powershell = (Get-Command powershell).Source
$logRoot = Join-Path $repoRoot "logs\mcp"
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) { Write-Host "[X] Run this elevated to REGISTER the task (the server itself runs unelevated)." -ForegroundColor Red; exit 1 }

$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { Write-Host "[X] Python 3 not found on PATH." -ForegroundColor Red; exit 1 }
$server = Join-Path $here "docstruct_mcp_server.py"
$action = New-McpScheduledTaskAction -PowerShellPath $powershell -LauncherPath $launcher `
    -PythonPath $py -ServerPath $server -WorkingDirectory $here -Name "docstruct" -LogDirectory $logRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -RunLevel Limited -LogonType Interactive
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "mcp-docstruct" -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "[OK] Registered scheduled task 'mcp-docstruct' (UNELEVATED, at logon)." -ForegroundColor Green
Write-Host "     Start now: Start-ScheduledTask -TaskName mcp-docstruct" -ForegroundColor Cyan
Write-Host "     Remove:    .\uninstall_task.ps1" -ForegroundColor Cyan
