# Starts the Process-Inspection MCP server. Run elevated for full cross-process detail.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) {
  Write-Host "[!] Not elevated. who_locks / wait_chain / handle ranking work; some cross-process detail (protected processes) will be AccessDenied." -ForegroundColor Yellow
}
$py = (Get-Command python).Source
$env:PYTHONIOENCODING = "utf-8"
Write-Host "[*] Starting Process-Inspection MCP on http://127.0.0.1:8785/mcp  (python: $py)" -ForegroundColor Cyan
& $py (Join-Path $here "procinspect_mcp_server.py")
