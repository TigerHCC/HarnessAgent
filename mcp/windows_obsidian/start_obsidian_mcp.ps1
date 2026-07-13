# Starts the Obsidian MCP server. Does NOT need Administrator (it only reads/writes user files).
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { Write-Host "[X] Python 3 not found on PATH." -ForegroundColor Red; exit 1 }
$env:PYTHONIOENCODING = "utf-8"
Write-Host "[*] Starting Obsidian MCP on http://127.0.0.1:8790/mcp  (python: $py)" -ForegroundColor Cyan
& $py (Join-Path $here "obsidian_mcp_server.py")
