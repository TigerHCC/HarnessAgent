function Assert-McpValueHasNoDoubleQuote {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value,

        [Parameter(Mandatory = $true)]
        [string]$ParameterName
    )

    if ($Value.Contains('"')) {
        throw "$ParameterName cannot contain a double quote."
    }
}

function New-McpLauncherArguments {
    param(
        [Parameter(Mandatory = $true)]
        [string]$LauncherPath,

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

    foreach ($Entry in @(
        @{ Name = "LauncherPath"; Value = $LauncherPath }
        @{ Name = "PythonPath"; Value = $PythonPath }
        @{ Name = "ServerPath"; Value = $ServerPath }
        @{ Name = "WorkingDirectory"; Value = $WorkingDirectory }
        @{ Name = "Name"; Value = $Name }
        @{ Name = "LogDirectory"; Value = $LogDirectory }
    )) {
        Assert-McpValueHasNoDoubleQuote -Value $Entry.Value -ParameterName $Entry.Name
    }

    return (('-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden ' +
        '-File "{0}" -PythonPath "{1}" -ServerPath "{2}" ' +
        '-WorkingDirectory "{3}" -Name "{4}" -LogDirectory "{5}"') -f
        $LauncherPath, $PythonPath, $ServerPath, $WorkingDirectory, $Name, $LogDirectory)
}

function New-McpScheduledTaskAction {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PowerShellPath,

        [Parameter(Mandatory = $true)]
        [string]$LauncherPath,

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

    Assert-McpValueHasNoDoubleQuote -Value $PowerShellPath -ParameterName "PowerShellPath"
    $Arguments = New-McpLauncherArguments -LauncherPath $LauncherPath `
        -PythonPath $PythonPath -ServerPath $ServerPath -WorkingDirectory $WorkingDirectory `
        -Name $Name -LogDirectory $LogDirectory

    return New-ScheduledTaskAction -Execute $PowerShellPath -Argument $Arguments -WorkingDirectory $WorkingDirectory
}

function Start-McpHiddenServer {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PowerShellPath,

        [Parameter(Mandatory = $true)]
        [string]$LauncherPath,

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

    Assert-McpValueHasNoDoubleQuote -Value $PowerShellPath -ParameterName "PowerShellPath"
    $Arguments = New-McpLauncherArguments -LauncherPath $LauncherPath `
        -PythonPath $PythonPath -ServerPath $ServerPath -WorkingDirectory $WorkingDirectory `
        -Name $Name -LogDirectory $LogDirectory

    return Start-Process -FilePath $PowerShellPath -ArgumentList $Arguments -WindowStyle Hidden -PassThru
}
