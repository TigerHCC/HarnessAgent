# Adds the markitdown extension to goose's config.yaml (idempotent; backs up first).
# This service is manifest-external, so setup_mcp_servers.ps1 does not manage it -- this script is
# the one-time registration step. Safe to re-run.
param([string]$ConfigPath = (Join-Path $env:APPDATA "Block\goose\config\config.yaml"))
$ErrorActionPreference = "Stop"
if (-not (Test-Path $ConfigPath)) { Write-Host "[X] goose config not found: $ConfigPath" -ForegroundColor Red; exit 1 }
$cfg = Get-Content $ConfigPath -Raw
if ($cfg -notmatch "(?m)^\s*extensions\s*:") { Write-Host "[X] no 'extensions:' section in $ConfigPath -- run setup_goose.ps1 first." -ForegroundColor Red; exit 1 }
if ($cfg -match "(?m)^\s{2}markitdown\s*:") { Write-Host "[OK] markitdown already present -- no change." -ForegroundColor Green; exit 0 }
Copy-Item $ConfigPath "$ConfigPath.bak-markitdown" -Force
$block = @"

  markitdown:
    type: streamable_http
    bundled: false
    name: markitdown
    enabled: true
    uri: http://127.0.0.1:8794/mcp
    headers: {}
    env_keys: []
    timeout: 120
    description: 'Convert documents (PDF, Office, images, audio, HTML, CSV, ZIP, YouTube, EPub) to Markdown via the official Microsoft markitdown-mcp server (manifest-external, 127.0.0.1:8794).'
"@
Add-Content -Path $ConfigPath -Value $block -Encoding UTF8
Write-Host "[OK] Added markitdown extension to $ConfigPath (backup: $ConfigPath.bak-markitdown)" -ForegroundColor Green
