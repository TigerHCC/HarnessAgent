# Starts the Filter-Stack MCP server elevated. Run as Administrator.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) {
  Write-Host "[X] Must run elevated. fltmc requires admin. Re-run this in an Administrator PowerShell." -ForegroundColor Red
  exit 1
}
$py = (Get-Command python).Source
$env:PYTHONIOENCODING = "utf-8"
Write-Host "[*] Starting Filter-Stack MCP on http://127.0.0.1:8787/mcp  (python: $py)" -ForegroundColor Cyan
& $py (Join-Path $here "filterstack_mcp_server.py")
