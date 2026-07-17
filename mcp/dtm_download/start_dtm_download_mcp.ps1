# Starts the DTM Download MCP server. Does NOT need Administrator (it only downloads into its own
# download_path from Artifactory).
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { Write-Host "[X] Python 3 not found on PATH." -ForegroundColor Red; exit 1 }
$env:PYTHONIOENCODING = "utf-8"
Write-Host "[*] Starting DTM Download MCP on http://127.0.0.1:8791/mcp  (python: $py)" -ForegroundColor Cyan
& $py (Join-Path $here "dtm_download_mcp_server.py")
