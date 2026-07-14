[CmdletBinding()]
param(
    [string]$OutputDir = (Join-Path $PSScriptRoot "reports\mcp"),
    [ValidateRange(1, 600)][int]$TimeoutSeconds = 15,
    [string]$ManifestPath = (Join-Path $PSScriptRoot "config\mcp_servers.json")
)

$ErrorActionPreference = "Stop"
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) {
    Write-Error "Python 3 not found on PATH."
    exit 2
}

$engine = Join-Path $PSScriptRoot "scripts\test_mcp_servers.py"
$arguments = @(
    $engine,
    "--manifest", $ManifestPath,
    "--output-dir", $OutputDir,
    "--timeout", $TimeoutSeconds
)
& $py @arguments
exit $LASTEXITCODE
