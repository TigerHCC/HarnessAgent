# Registers a Scheduled Task that runs the DTM Deploy MCP server elevated at logon. Run as Administrator.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent (Split-Path -Parent $here)
$launcher = Join-Path $repoRoot "scripts\start_mcp_hidden.ps1"
. (Join-Path $repoRoot "scripts\mcp_task_helpers.ps1")
$powershell = (Get-Command powershell).Source
$logRoot = Join-Path $repoRoot "logs\mcp"
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) { Write-Host "[X] Run this elevated (Administrator)." -ForegroundColor Red; exit 1 }

$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { Write-Host "[X] Python 3 not found on PATH." -ForegroundColor Red; exit 1 }
$server = Join-Path $here "dtm_deploy_mcp_server.py"
$action = New-McpScheduledTaskAction -PowerShellPath $powershell -LauncherPath $launcher `
    -PythonPath $py -ServerPath $server -WorkingDirectory $here -Name "dtm_deploy" -LogDirectory $logRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -RunLevel Highest -LogonType Interactive
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "mcp-dtm_deploy" -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "[OK] Registered scheduled task 'mcp-dtm_deploy' (elevated, at logon)." -ForegroundColor Green
Write-Host "     Start now: Start-ScheduledTask -TaskName mcp-dtm_deploy" -ForegroundColor Cyan
Write-Host "     Remove:    .\uninstall_task.ps1" -ForegroundColor Cyan
