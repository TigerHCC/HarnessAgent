# Starts the DTM SDK MCP server elevated. Run as Administrator.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) {
  Write-Host "[X] Must run elevated. The DTP sample utilities require Administrator." -ForegroundColor Red
  exit 1
}
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { Write-Host "[X] Python 3 not found on PATH." -ForegroundColor Red; exit 1 }
$env:PYTHONIOENCODING = "utf-8"
Write-Host "[*] Starting DTM SDK MCP on http://127.0.0.1:8789/mcp  (python: $py)" -ForegroundColor Cyan
& $py (Join-Path $here "dtm_sdk_mcp_server.py")
