# Starts the Windows Update-history MCP server. Elevation not required (QueryHistory/Get-HotFix work as user).
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = (Get-Command python).Source
$env:PYTHONIOENCODING = "utf-8"
Write-Host "[*] Starting Windows Update MCP on http://127.0.0.1:8788/mcp  (python: $py)" -ForegroundColor Cyan
& $py (Join-Path $here "winupdate_mcp_server.py")
