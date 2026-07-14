[CmdletBinding()]
param(
    [string]$OutputDir,
    [ValidateRange(1, 600)][int]$TimeoutSeconds = 15,
    [string]$ManifestPath
)

$ErrorActionPreference = "Stop"
$scriptRoot = (Resolve-Path -LiteralPath $PSScriptRoot).Path
if (-not $OutputDir) {
    $OutputDir = Join-Path $scriptRoot "reports\mcp"
}
if (-not $ManifestPath) {
    $ManifestPath = Join-Path $scriptRoot "config\mcp_servers.json"
}

$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) {
    Write-Error "Python 3 not found on PATH."
    exit 2
}

$engine = Join-Path $scriptRoot "scripts\test_mcp_servers.py"
$arguments = @(
    $engine,
    "--manifest", $ManifestPath,
    "--output-dir", $OutputDir,
    "--timeout", $TimeoutSeconds
)
& $py @arguments
exit $LASTEXITCODE
