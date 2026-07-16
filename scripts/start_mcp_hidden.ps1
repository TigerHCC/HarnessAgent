[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PythonPath,

    [Parameter(Mandatory = $true)]
    [string]$ServerPath,

    [Parameter(Mandatory = $true)]
    [string]$WorkingDirectory,

    [Parameter(Mandatory = $true)]
    [string]$Name,

    [Parameter(Mandatory = $true)]
    [string]$LogDirectory
)

$ErrorActionPreference = "Stop"

function Rotate-Log([string]$Path) {
    if ((Test-Path -LiteralPath $Path) -and
        (Get-Item -LiteralPath $Path).Length -gt 10MB) {
        Move-Item -LiteralPath $Path -Destination "$Path.1" -Force
    }
}

if ($Name -notmatch '^[A-Za-z0-9._-]+$') {
    throw "Invalid MCP name: $Name"
}

$ResolvedPython = (Resolve-Path -LiteralPath $PythonPath).Path
$ResolvedServer = (Resolve-Path -LiteralPath $ServerPath).Path
$ResolvedWorkingDirectory = (Resolve-Path -LiteralPath $WorkingDirectory).Path

New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
$ResolvedLogDirectory = (Resolve-Path -LiteralPath $LogDirectory).Path
$StdoutLog = Join-Path $ResolvedLogDirectory "$Name.stdout.log"
$StderrLog = Join-Path $ResolvedLogDirectory "$Name.stderr.log"

Rotate-Log $StdoutLog
Rotate-Log $StderrLog

$env:PYTHONIOENCODING = "utf-8"
$VariablePrefix = "MCP_LAUNCHER_$([Guid]::NewGuid().ToString('N'))"
$PythonVariable = "${VariablePrefix}_PYTHON"
$ServerVariable = "${VariablePrefix}_SERVER"
$StdoutVariable = "${VariablePrefix}_STDOUT"
$StderrVariable = "${VariablePrefix}_STDERR"
$PathVariables = @{
    $PythonVariable = $ResolvedPython
    $ServerVariable = $ResolvedServer
    $StdoutVariable = $StdoutLog
    $StderrVariable = $StderrLog
}

foreach ($Variable in $PathVariables.GetEnumerator()) {
    [Environment]::SetEnvironmentVariable($Variable.Key, $Variable.Value, "Process")
}

$Command = ('"%{0}%" "%{1}%" 1>> "%{2}%" 2>> "%{3}%"' -f
    $PythonVariable, $ServerVariable, $StdoutVariable, $StderrVariable)
try {
    Push-Location -LiteralPath $ResolvedWorkingDirectory
    try {
        & $env:ComSpec /d /v:off /s /c $Command
        $ExitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}
finally {
    foreach ($VariableName in $PathVariables.Keys) {
        [Environment]::SetEnvironmentVariable($VariableName, $null, "Process")
    }
}

exit $ExitCode
