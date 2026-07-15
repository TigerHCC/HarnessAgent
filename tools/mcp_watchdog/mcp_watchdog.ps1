<#
.SYNOPSIS
  Liveness watchdog for the local MCP servers. Detects a WEDGED server (port still listening but the
  event loop stopped answering) and restarts it via its Scheduled Task.

.DESCRIPTION
  A FastMCP server runs a single asyncio event loop. If that loop blocks, the TCP port keeps listening
  (so a naive "port UP" check passes) but NO request is ever answered -- and because Goose initializes
  all MCP extensions in parallel and waits for every one, a single wedged server hangs the whole
  harness (CLI and goose_web). See docs/HARDENING_BACKLOG.md.

  This watchdog probes each server with a cheap RAW HTTP GET to /mcp (a healthy endpoint answers
  400/406 instantly; a wedged one times out). Anything that doesn't answer within -TimeoutSec is
  restarted: kill the owning PID (if any) + Start-ScheduledTask for its task. One pass per invocation;
  install_watchdog.ps1 schedules it to run every few minutes.

  The name/port/task inventory is loaded from config/mcp_servers.json, the same manifest used by setup.

.PARAMETER DryRun     Probe and report only; never kill/restart. Safe to run anytime.
.PARAMETER TimeoutSec Per-probe timeout (default 6). No answer within this -> treated as wedged.
.PARAMETER ManifestPath Override config/mcp_servers.json (default: repo root config).
.PARAMETER InventoryOnly Validate the manifest and emit name/port/task JSON without probing or restarting.
.PARAMETER LogPath    Override the log file (default: tools\mcp_watchdog\watchdog.log).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\mcp_watchdog.ps1 -DryRun
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\mcp_watchdog.ps1        # probe + restart wedged, log
#>
[CmdletBinding()]
param(
  [switch]$DryRun,
  [switch]$InventoryOnly,
  [int]$TimeoutSec = 6,
  [string]$ManifestPath,
  [string]$LogPath
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ManifestPath) { $ManifestPath = Join-Path (Split-Path -Parent $here) "..\config\mcp_servers.json" }
if (-not $LogPath)   { $LogPath   = Join-Path $here "watchdog.log" }

# --- load the same validated manifest inventory used by setup_mcp_servers.ps1 ---
function Get-McpRegistry([string]$manifestPath) {
  if (-not (Test-Path -LiteralPath $manifestPath)) { throw "MCP manifest not found at $manifestPath" }
  try {
    $decoded = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    $entries = @($decoded | ForEach-Object { $_ })
  }
  catch { throw "Invalid MCP manifest at ${manifestPath}: $_" }
  if ($entries.Count -ne 14) { throw "MCP manifest must contain exactly 14 entries on canonical ports 8777-8790; found $($entries.Count) entries." }
  $out = @()
  $seenNames = @{}
  $seenPorts = @{}
  $seenTasks = @{}
  $textFields = @("name","directory","task","run_level","description","health_tool")
  $integerTypes = @([byte],[sbyte],[int16],[uint16],[int32],[uint32],[int64],[uint64])
  $expectedPorts = @(8777..8790)
  foreach ($entry in $entries) {
    foreach ($field in @("name","directory","port","task","run_level","description","health_tool")) {
      if ($null -eq $entry.$field) {
        throw "MCP manifest entry is missing '$field'."
      }
    }
    foreach ($field in $textFields) {
      if (-not ($entry.$field -is [string]) -or [string]::IsNullOrWhiteSpace($entry.$field)) {
        throw "MCP manifest entry has invalid '$field'; expected a non-empty string."
      }
    }
    if ($integerTypes -notcontains $entry.port.GetType()) {
      throw "MCP manifest entry has invalid 'port'; expected an integer."
    }
    if ($entry.run_level -notin @("Highest","Limited")) {
      throw "Invalid run_level for $($entry.name): $($entry.run_level)"
    }
    if ($entry.port -notin $expectedPorts) {
      throw "MCP manifest must use canonical ports 8777-8790 exactly once."
    }
    if ($seenNames.ContainsKey($entry.name)) { throw "MCP manifest contains duplicate name: $($entry.name)" }
    if ($seenPorts.ContainsKey($entry.port)) { throw "MCP manifest contains duplicate port: $($entry.port)" }
    if ($seenTasks.ContainsKey($entry.task)) { throw "MCP manifest contains duplicate task: $($entry.task)" }
    $seenNames[$entry.name] = $true
    $seenPorts[$entry.port] = $true
    $seenTasks[$entry.task] = $true
    $out += [pscustomobject]@{ name=[string]$entry.name; port=[int]$entry.port; task=[string]$entry.task }
  }
  $actualPorts = @($out.port | Sort-Object)
  $portDifference = @(Compare-Object -ReferenceObject $expectedPorts -DifferenceObject $actualPorts)
  if ($out.Count -ne 14 -or $portDifference.Count) {
    $foundPorts = if ($actualPorts.Count) { $actualPorts -join ", " } else { "none" }
    throw "MCP manifest must contain exactly 14 entries on canonical ports 8777-8790; found $($out.Count) entries on ports: $foundPorts"
  }
  return $out
}

# --- liveness probe: does the server answer an HTTP request at all? ---
# Returns 'alive' (got any HTTP status), 'wedged' (listening but no answer within timeout), or
# 'down' (not listening / connection refused).
function Test-McpAlive([int]$port, [int]$timeoutSec) {
  $listening = [bool](Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
  try {
    Invoke-WebRequest "http://127.0.0.1:$port/mcp" -TimeoutSec $timeoutSec -UseBasicParsing | Out-Null
    return 'alive'   # 200 (unlikely) -- still an answer
  } catch {
    if ($_.Exception.Response) { return 'alive' }              # 400/406/etc = event loop answered
    if (-not $listening)       { return 'down' }               # nothing on the port
    return 'wedged'                                            # listening but no HTTP answer in time
  }
}

function Write-Log([string]$msg) {
  $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  try { Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8 } catch {}
  Write-Host $line
}

function Restart-Mcp($entry) {
  # kill whatever holds the port (the wedged process), then start a fresh instance via its task.
  $conns = Get-NetTCPConnection -State Listen -LocalPort $entry.port -ErrorAction SilentlyContinue
  foreach ($c in $conns) {
    try { Stop-Process -Id $c.OwningProcess -Force -ErrorAction Stop; Write-Log "  killed PID $($c.OwningProcess) (port $($entry.port))" }
    catch { Write-Log "  could not kill PID $($c.OwningProcess): $_" }
  }
  try {
    Start-ScheduledTask -TaskName $entry.task -ErrorAction Stop
    Write-Log "  started Scheduled Task '$($entry.task)'"
  } catch { Write-Log "  FAILED to start task '$($entry.task)': $_" }
}

# --- main pass ---
$registry = @(Get-McpRegistry $ManifestPath)
if ($InventoryOnly) {
  ConvertTo-Json -InputObject @($registry) -Depth 3
  exit 0
}
$wedged = @()
$report = @()
foreach ($m in $registry) {
  $state = Test-McpAlive $m.port $TimeoutSec
  $report += "{0,-12} {1} {2}" -f $m.name, $m.port, $state.ToUpper()
  if ($state -ne 'alive') { $wedged += [pscustomobject]@{ entry=$m; state=$state } }
}

Write-Host "=== MCP watchdog pass ($($registry.Count) servers, timeout ${TimeoutSec}s$(if($DryRun){', DRY-RUN'})) ==="
$report | ForEach-Object { Write-Host "  $_" }

if (-not $wedged.Count) { Write-Host "All servers answering. No action."; exit 0 }

foreach ($w in $wedged) {
  $verb = if ($w.state -eq 'wedged') { "WEDGED (listening, not answering)" } else { "DOWN (not listening)" }
  if ($DryRun) {
    Write-Host "[DRY-RUN] would restart $($w.entry.name) (port $($w.entry.port)) -- $verb"
  } else {
    Write-Log "RESTART $($w.entry.name) (port $($w.entry.port), task $($w.entry.task)) -- $verb"
    Restart-Mcp $w.entry
  }
}
exit 0
