# Starts the DTM Deploy MCP server. Needs Administrator (msiexec, HKLM writes, service control).
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { Write-Host "[X] Python 3 not found on PATH." -ForegroundColor Red; exit 1 }
$env:PYTHONIOENCODING = "utf-8"
Write-Host "[*] Starting DTM Deploy MCP on http://127.0.0.1:8792/mcp  (python: $py)" -ForegroundColor Cyan
& $py (Join-Path $here "dtm_deploy_mcp_server.py")
