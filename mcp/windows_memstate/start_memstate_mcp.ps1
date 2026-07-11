# Starts the Memory-State MCP server elevated. Run as Administrator.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) {
  Write-Host "[!] Not elevated. Pool tags work; the memory-list composition may fail without SeProfileSingleProcessPrivilege." -ForegroundColor Yellow
}
$py = (Get-Command python).Source
$env:PYTHONIOENCODING = "utf-8"
Write-Host "[*] Starting Memory-State MCP on http://127.0.0.1:8786/mcp  (python: $py)" -ForegroundColor Cyan
& $py (Join-Path $here "memstate_mcp_server.py")
