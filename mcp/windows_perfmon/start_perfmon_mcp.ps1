# Starts the Perfmon MCP server. Run elevated (a few counters like Thermal need admin; most do not).
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = (Get-Command python).Source
$env:PYTHONIOENCODING = "utf-8"
Write-Host "[*] Starting Perfmon MCP on http://127.0.0.1:8783/mcp  (python: $py)" -ForegroundColor Cyan
& $py (Join-Path $here "perfmon_mcp_server.py")
