<#
  Launch the Goose Harness web UI (PowerShell bridge -> `goose run`).
  Open http://<this-box-ip>:8799 from any machine on the LAN.

  Examples:
    .\serve_web.ps1                              # bind 0.0.0.0:8799 (LAN), no token
    $env:GOOSE_WEB_TOKEN='mysecret'; .\serve_web.ps1   # require a token (recommended for LAN)
    $env:GOOSE_WEB_HOST='127.0.0.1'; .\serve_web.ps1   # local-only (no admin/urlacl needed)
    $env:GOOSE_WEB_PORT='9000'; .\serve_web.ps1

  Server settings live in config.json (port, token, backends layout, ...); env vars
  override it. The model/provider shown in the UI are read LIVE from goose's own
  config.yaml on every health poll — the UI always shows what goose actually uses.
  If running unsigned scripts is blocked:
    powershell -ExecutionPolicy Bypass -File .\serve_web.ps1
#>
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$Here = $PSScriptRoot
$env:GOOSE_TELEMETRY_ENABLED = 'false'   # privacy: no goose usage-telemetry upload (env overrides config)

# sanity: is goose reachable? (config.json / GOOSE_BIN can point elsewhere)
$goose = $env:GOOSE_BIN
if (-not $goose) {
    $cand = Join-Path $HOME '.local\bin\goose.exe'
    if (Test-Path -LiteralPath $cand) { $goose = $cand }
    elseif (Get-Command goose -ErrorAction SilentlyContinue) { $goose = (Get-Command goose).Source }
    elseif (Get-Command goose.exe -ErrorAction SilentlyContinue) { $goose = (Get-Command goose.exe).Source }
}
if (-not $goose) {
    Write-Warning "goose not found (set GOOSE_BIN or 'goose_bin' in config.json, or add goose to PATH). Starting anyway; /api/chat will fail until goose is reachable."
}

& (Join-Path $Here 'server.ps1')
