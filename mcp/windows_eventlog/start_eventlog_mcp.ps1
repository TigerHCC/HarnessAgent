# Starts the Event Log MCP server elevated. Run as Administrator.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) {
  Write-Host "[X] Must run elevated. Open PowerShell as Administrator, then re-run this script." -ForegroundColor Red
  Write-Host "    (The Security log needs admin; System/Application would work but the server runs elevated by design.)" -ForegroundColor Yellow
  exit 1
}
$py = (Get-Command python).Source
$env:PYTHONIOENCODING = "utf-8"
Write-Host "[*] Starting Event Log MCP on http://127.0.0.1:8778/mcp  (python: $py)" -ForegroundColor Cyan
& $py (Join-Path $here "eventlog_mcp_server.py")
