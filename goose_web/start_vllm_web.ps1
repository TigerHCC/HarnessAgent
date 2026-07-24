$ErrorActionPreference = 'Stop'

# A foreground Windows-side WSL client keeps the Ubuntu VM alive. Without it,
# this machine may power down WSL while Docker/vLLM is still initializing.
$keepalive = Get-CimInstance Win32_Process -Filter "Name='wsl.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -match 'Ubuntu.*sleep.*infinity' } |
  Select-Object -First 1
if (-not $keepalive) {
  Start-Process -FilePath 'wsl.exe' -WindowStyle Hidden `
    -ArgumentList @('-d', 'Ubuntu', '--', 'sleep', 'infinity')
  Start-Sleep -Seconds 2
}

$compose = '/mnt/c/Users/Dell/Downloads/DTMAgentic/HarnessAgent/HarnessAgent-LLM-main/docker-compose.windows.llamacpp.yaml'
& wsl.exe -d Ubuntu -- docker compose -f $compose up -d qwen-chat qwen-embed-cpu
if ($LASTEXITCODE -ne 0) { throw "Failed to start the vLLM qwen-chat service." }

# First installation can take a long time while the 12.36 GB checkpoint downloads.
$ready = $false
for ($i = 0; $i -lt 360; $i++) {
  try {
    Invoke-RestMethod 'http://127.0.0.1:8000/health' -TimeoutSec 3 | Out-Null
    $ready = $true
    break
  } catch {
    Start-Sleep -Seconds 5
  }
}
if (-not $ready) { throw "vLLM did not become healthy on port 8000." }

$env:GOOSE_PROVIDER = 'openai'
$env:GOOSE_MODEL = 'qwen3.6-27b-q3ks'
$env:OPENAI_HOST = 'http://127.0.0.1:8000'
$env:OPENAI_BASE_PATH = 'v1/chat/completions'
$env:OPENAI_API_KEY = 'sk-local'
$env:GOOSE_CONTEXT_LIMIT = '163840'
$env:GOOSE_TELEMETRY_ENABLED = 'false'
$env:GOOSE_WEB_CONFIG = Join-Path $PSScriptRoot 'config.WINDOWS_ALIENWARE.json'
Set-Location -LiteralPath $PSScriptRoot

# Agent profiles and schedule management are currently implemented by the
# Windows-native backend. server.py does not expose /api/profiles.
& (Join-Path $PSScriptRoot 'server.ps1')
