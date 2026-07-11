# Starts the Crash/WER MCP server elevated. Run as Administrator.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) {
  Write-Host "[X] Must run elevated. Open PowerShell as Administrator, then re-run this script." -ForegroundColor Red
  Write-Host "    (Kernel dumps + some WER folders need admin; the server runs elevated by design.)" -ForegroundColor Yellow
  exit 1
}
$py = (Get-Command python).Source
$env:PYTHONIOENCODING = "utf-8"
Write-Host "[*] Starting Crash/WER MCP on http://127.0.0.1:8779/mcp  (python: $py)" -ForegroundColor Cyan
& $py (Join-Path $here "crash_mcp_server.py")
