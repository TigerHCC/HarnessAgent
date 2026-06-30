<#
.SYNOPSIS
  One-click setup of the Goose agent harness on a Windows machine, pointed at the
  GB10 model server. Installs the Goose CLI, writes config.yaml (vLLM primary +
  Ollama fallback), verifies connectivity, and runs a tool-calling smoke test.

.DESCRIPTION
  Mirrors the validated install from HarnessAgent/docs/install_results.md.
  Safe to re-run (idempotent): re-downloads the binary and overwrites config.

.PARAMETER Gb10Host
  IP/hostname of the GB10 model server. Default 192.168.86.44.

.PARAMETER Backend
  Active model backend: 'vllm' (default, fast, OpenAI-compat :8000) or 'ollama' (:11434).

.PARAMETER GooseVersion
  Release tag to install ('stable' default, or e.g. 'v1.39.0').

.PARAMETER SkipSmokeTest
  Skip the final goose tool-calling smoke test.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\setup_goose.ps1
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\setup_goose.ps1 -Backend ollama -Gb10Host 192.168.86.44
#>
[CmdletBinding()]
param(
  [string]$Gb10Host = "192.168.86.44",
  [ValidateSet("vllm","ollama")][string]$Backend = "vllm",
  [string]$GooseVersion = "stable",
  [switch]$SkipSmokeTest
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
function Info($m){ Write-Host "[*] $m" -ForegroundColor Cyan }
function Ok($m){ Write-Host "[OK] $m" -ForegroundColor Green }
function Warn($m){ Write-Host "[!] $m" -ForegroundColor Yellow }
function Die($m){ Write-Host "[X] $m" -ForegroundColor Red; exit 1 }

Write-Host "=== Goose harness setup (Windows -> GB10 $Gb10Host, backend=$Backend) ===" -ForegroundColor Magenta

# --- 0. Prereqs ---
if ($PSVersionTable.PSVersion.Major -lt 5) { Die "PowerShell 5+ required." }
$arch = $env:PROCESSOR_ARCHITECTURE
if ($arch -ne "AMD64") { Warn "Detected arch '$arch'. Goose Windows build is x86_64 only; continuing anyway." }

# --- 1. Endpoint URLs ---
$vllmBase   = "http://${Gb10Host}:8000"
$ollamaBase = "http://${Gb10Host}:11434"
$gooseExe   = Join-Path $env:USERPROFILE ".local\bin\goose.exe"
$binDir     = Split-Path $gooseExe -Parent

# --- 2. Check GB10 connectivity (non-fatal) ---
$backendUp = $false
if ($Backend -eq "vllm") {
  Info "Checking vLLM at $vllmBase/v1/models ..."
  try { $m = Invoke-RestMethod "$vllmBase/v1/models" -TimeoutSec 8; Ok ("vLLM up: " + (($m.data|%{$_.id}) -join ", ")); $backendUp = $true }
  catch { Warn "vLLM unreachable: $($_.Exception.Message). Will still install + write config." }
} else {
  Info "Checking Ollama at $ollamaBase/api/tags ..."
  try { $t = Invoke-RestMethod "$ollamaBase/api/tags" -TimeoutSec 8; Ok ("Ollama up: $($t.models.Count) models"); $backendUp = $true }
  catch { Warn "Ollama unreachable: $($_.Exception.Message). Will still install + write config." }
}

# --- 3. Download Goose release zip ---
$zipName = "goose-x86_64-pc-windows-msvc.zip"
$dlUrl   = "https://github.com/aaif-goose/goose/releases/download/$GooseVersion/$zipName"
$tmp     = Join-Path $env:TEMP ("goose_setup_" + [System.Guid]::NewGuid().ToString("N").Substring(0,8))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$zipPath = Join-Path $tmp $zipName
Info "Downloading $dlUrl ..."
try { Invoke-WebRequest -Uri $dlUrl -OutFile $zipPath -UseBasicParsing -TimeoutSec 300 }
catch { Die "Download failed: $($_.Exception.Message)" }
Ok ("Downloaded {0:N1} MB" -f ((Get-Item $zipPath).Length/1MB))

# --- 4. Extract + install ---
Info "Extracting..."
$extract = Join-Path $tmp "x"
Expand-Archive -Path $zipPath -DestinationPath $extract -Force
$srcDir = $extract
if (Test-Path (Join-Path $extract "goose-package")) { $srcDir = Join-Path $extract "goose-package" }
$srcExe = Join-Path $srcDir "goose.exe"
if (-not (Test-Path $srcExe)) { Die "goose.exe not found in archive." }
New-Item -ItemType Directory -Force -Path $binDir | Out-Null
Copy-Item $srcExe $gooseExe -Force
Get-ChildItem $srcDir -Filter *.dll -ErrorAction SilentlyContinue | ForEach-Object { Copy-Item $_.FullName (Join-Path $binDir $_.Name) -Force }
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
$ver = (& $gooseExe --version) -join " "
Ok "Installed goose ($ver) -> $gooseExe"

# --- 5. PATH ---
$userPath = [Environment]::GetEnvironmentVariable('PATH','User')
if ($userPath -notlike "*$binDir*") {
  $new = if ([string]::IsNullOrEmpty($userPath)) { $binDir } else { $userPath.TrimEnd(';') + ';' + $binDir }
  [Environment]::SetEnvironmentVariable('PATH', $new, 'User')
  Ok "Added $binDir to User PATH (restart terminals to pick it up)."
} else { Ok "$binDir already on User PATH." }
$env:PATH += ";$binDir"

# --- 6. Write config.yaml ---
$cfgDir = Join-Path $env:APPDATA "Block\goose\config"
New-Item -ItemType Directory -Force -Path $cfgDir | Out-Null
$cfgPath = Join-Path $cfgDir "config.yaml"

if ($Backend -eq "vllm") { $provLines = @"
GOOSE_PROVIDER: openai
GOOSE_MODEL: qwen-3.6-chat
OPENAI_HOST: $vllmBase
OPENAI_BASE_PATH: v1/chat/completions
OPENAI_API_KEY: sk-local
# Fallback (uncomment to use Ollama):
# GOOSE_PROVIDER: ollama
# GOOSE_MODEL: qwen3.5:9b
OLLAMA_HOST: $ollamaBase
OLLAMA_TIMEOUT: 900
GOOSE_STREAM_TIMEOUT: 900
OLLAMA_STREAM_TIMEOUT: 900
"@ } else { $provLines = @"
GOOSE_PROVIDER: ollama
GOOSE_MODEL: qwen3.5:9b
OLLAMA_HOST: $ollamaBase
OLLAMA_TIMEOUT: 900
GOOSE_STREAM_TIMEOUT: 900
OLLAMA_STREAM_TIMEOUT: 900
# Fallback (uncomment to use vLLM):
# GOOSE_PROVIDER: openai
# GOOSE_MODEL: qwen-3.6-chat
# OPENAI_HOST: $vllmBase
# OPENAI_BASE_PATH: v1/chat/completions
# OPENAI_API_KEY: sk-local
"@ }

$config = @"
# Goose CLI configuration - generated by HarnessAgent/setup_goose.ps1
# Goose runs on this Windows machine; models served on GB10 ($Gb10Host).
# vLLM requires GB10 launched with --enable-auto-tool-choice --tool-call-parser qwen3_coder
# (see HarnessAgent/config/docker-compose.yaml). Ollama needs no GB10 flags.

$provLines

# Privacy: disable goose's usage telemetry. goose otherwise POSTs usage metadata
# (model, extensions, session names, token/session counts, settings) to a hosted
# PostHog endpoint (us.i.posthog.com). Keep this false so no data leaves this
# machine without approval. Your prompts/responses always stay on your provider
# (here the local vLLM/Ollama). See docs/install_results.md "Telemetry / privacy".
GOOSE_TELEMETRY_ENABLED: false

extensions:
  developer:
    type: builtin
    bundled: true
    name: developer
    enabled: true
    timeout: 300
  memory:
    type: stdio
    bundled: false
    name: memory
    enabled: true
    cmd: $gooseExe
    args:
      - mcp
      - memory
    env_keys: []
    timeout: 300
    description: Example stdio MCP extension (goose bundled 'memory' server)
"@
Set-Content -Path $cfgPath -Value $config -Encoding UTF8
Ok "Wrote config -> $cfgPath (active backend: $Backend)"

# --- 7. Smoke test ---
if ($SkipSmokeTest) { Warn "Smoke test skipped (-SkipSmokeTest)." }
elseif (-not $backendUp) { Warn "Backend was unreachable; skipping smoke test. Re-run once GB10 is up." }
else {
  Info "Smoke test: headless tool-calling (may take ~10s-2min depending on model load)..."
  $work = Join-Path $env:TEMP "goose-setup-check"
  if (Test-Path $work) { Remove-Item $work -Recurse -Force }
  New-Item -ItemType Directory -Force -Path $work | Out-Null
  Push-Location $work
  $env:GOOSE_MODE = "auto"
  $env:GOOSE_TELEMETRY_ENABLED = "false"   # privacy: no usage-telemetry upload during the smoke test
  try {
    & $gooseExe run --no-session --max-turns 6 -t "Create ./ok.txt containing the word READY, then stop." 2>&1 | Out-Host
  } catch { Warn "Smoke test run error: $($_.Exception.Message)" }
  Pop-Location
  if (Test-Path (Join-Path $work "ok.txt")) { Ok ("Smoke test PASSED -> " + (Get-Content (Join-Path $work "ok.txt") -Raw).Trim()) }
  else { Warn "Smoke test did NOT create the file. Check backend/tool-calling (see install_results.md)." }
}

Write-Host ""
Ok "Done. Use it with:  `$env:GOOSE_MODE='auto'; goose run --no-session -t `"your task`""
Write-Host "    Interactive: goose session   (run in your own terminal)" -ForegroundColor DarkGray
