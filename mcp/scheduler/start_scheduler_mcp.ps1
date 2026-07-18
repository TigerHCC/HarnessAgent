# Starts the Scheduler MCP server. Does NOT need Administrator (it only drives goose + writes its own state).
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { Write-Host "[X] Python 3 not found on PATH." -ForegroundColor Red; exit 1 }
$env:PYTHONIOENCODING = "utf-8"
Write-Host "[*] Starting Scheduler MCP on http://127.0.0.1:8793/mcp  (python: $py)" -ForegroundColor Cyan
& $py (Join-Path $here "scheduler_mcp_server.py")
