[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPath,

    [Parameter(Mandatory = $true)]
    [ValidateSet("Standalone", "Suite")]
    [string]$Mode,

    [Parameter(Mandatory = $true)]
    [string]$PythonPath,

    [Parameter(Mandatory = $true)]
    [string]$PowerShellPath,

    [switch]$SkipTasks,
    [switch]$SkipWatchdog,
    [switch]$NoStart
)

$ErrorActionPreference = "Stop"
$global:McpHarnessRegistrations = [System.Collections.Generic.List[object]]::new()
$global:McpHarnessStarts = [System.Collections.Generic.List[object]]::new()

function Get-Command {
    [CmdletBinding()]
    param([Parameter(Position = 0)][string]$Name)

    if ($Name -eq "python") { return [pscustomobject]@{ Source = $PythonPath } }
    if ($Name -eq "powershell") { return [pscustomobject]@{ Source = $PowerShellPath } }
    throw "Unexpected Get-Command call: $Name"
}

function New-Object {
    param(
        [Parameter(Position = 0, Mandatory = $true)]
        [string]$TypeName,
        [Parameter(ValueFromRemainingArguments = $true)]
        [object[]]$ArgumentList
    )

    if ($TypeName -like "Security.Principal.WindowsPrincipal*") {
        $principal = [pscustomobject]@{}
        $principal | Add-Member -MemberType ScriptMethod -Name IsInRole -Value { return $true }
        return $principal
    }
    if ($ArgumentList.Count) {
        return Microsoft.PowerShell.Utility\New-Object -TypeName $TypeName -ArgumentList $ArgumentList
    }
    return Microsoft.PowerShell.Utility\New-Object -TypeName $TypeName
}

function New-ScheduledTaskAction {
    param([string]$Execute, [string]$Argument, [string]$WorkingDirectory)
    return [pscustomobject]@{
        Execute = $Execute
        Argument = $Argument
        WorkingDirectory = $WorkingDirectory
    }
}

function New-ScheduledTaskTrigger {
    param(
        [switch]$AtLogOn,
        [switch]$Once,
        [datetime]$At,
        [timespan]$RepetitionInterval,
        [timespan]$RepetitionDuration
    )
    return [pscustomobject]@{
        AtLogOn = [bool]$AtLogOn
        Once = [bool]$Once
        Repetition = [pscustomobject]@{
            Interval = $RepetitionInterval
            Duration = $RepetitionDuration
        }
    }
}

function New-ScheduledTaskPrincipal {
    param([string]$UserId, [string]$RunLevel, [string]$LogonType)
    return [pscustomobject]@{
        UserId = $UserId
        RunLevel = $RunLevel
        LogonType = $LogonType
    }
}

function New-ScheduledTaskSettingsSet {
    param(
        [switch]$AllowStartIfOnBatteries,
        [switch]$DontStopIfGoingOnBatteries,
        [switch]$StartWhenAvailable,
        [timespan]$ExecutionTimeLimit,
        [string]$MultipleInstances
    )
    return [pscustomobject]@{
        AllowStartIfOnBatteries = [bool]$AllowStartIfOnBatteries
        DontStopIfGoingOnBatteries = [bool]$DontStopIfGoingOnBatteries
        StartWhenAvailable = [bool]$StartWhenAvailable
    }
}

function Register-ScheduledTask {
    param(
        [string]$TaskName,
        [object]$Action,
        [object]$Trigger,
        [object]$Principal,
        [object]$Settings,
        [switch]$Force
    )
    $global:McpHarnessRegistrations.Add([pscustomobject]@{
        TaskName = $TaskName
        Action = $Action
        Trigger = $Trigger
        Principal = $Principal
        Settings = $Settings
        Force = [bool]$Force
    })
}

function Start-Process {
    param(
        [string]$FilePath,
        [string]$ArgumentList,
        [string]$WindowStyle,
        [switch]$PassThru
    )
    $name = if ($ArgumentList -match '-Name "([^"]+)"') { $Matches[1] } else { $null }
    $start = [pscustomobject]@{
        Name = $name
        Execute = $FilePath
        Argument = $ArgumentList
        WorkingDirectory = if ($ArgumentList -match '-WorkingDirectory "([^"]+)"') { $Matches[1] } else { $null }
        WindowStyle = $WindowStyle
        PassThru = [bool]$PassThru
    }
    $global:McpHarnessStarts.Add($start)
    return $start
}

function Get-NetTCPConnection { return }
function Start-Sleep { }
function schtasks { return }

if ($Mode -eq "Suite") {
    $suiteParameters = @{
        SkipDeps = $true
        SkipConfig = $true
        SkipSysmon = $true
        SkipTasks = [bool]$SkipTasks
        SkipWatchdog = [bool]$SkipWatchdog
        NoStart = [bool]$NoStart
    }
    & $InstallerPath @suiteParameters
}
else {
    & $InstallerPath
}

$result = [pscustomobject]@{
    CallerWorkingDirectory = (Get-Location).Path
    CurrentUser = "$env:USERDOMAIN\$env:USERNAME"
    Registrations = @($global:McpHarnessRegistrations)
    Starts = @($global:McpHarnessStarts)
}
Write-Output ("HARNESS_JSON:" + ($result | ConvertTo-Json -Depth 8 -Compress))
Remove-Variable -Scope Global -Name McpHarnessRegistrations, McpHarnessStarts
