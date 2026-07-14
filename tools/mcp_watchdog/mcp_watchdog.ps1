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

  The port -> task map is derived from setup_mcp_servers.ps1's $MCPS array (single source of truth), so
  it never drifts from the installed set.

.PARAMETER DryRun     Probe and report only; never kill/restart. Safe to run anytime.
.PARAMETER TimeoutSec Per-probe timeout (default 6). No answer within this -> treated as wedged.
.PARAMETER SetupPath  Override the path to setup_mcp_servers.ps1 (default: repo root).
.PARAMETER LogPath    Override the log file (default: tools\mcp_watchdog\watchdog.log).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\mcp_watchdog.ps1 -DryRun
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\mcp_watchdog.ps1        # probe + restart wedged, log
#>
[CmdletBinding()]
param(
  [switch]$DryRun,
  [int]$TimeoutSec = 6,
  [string]$SetupPath,
  [string]$LogPath
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $SetupPath) { $SetupPath = Join-Path (Split-Path -Parent $here) "..\setup_mcp_servers.ps1" }
if (-not $LogPath)   { $LogPath   = Join-Path $here "watchdog.log" }

# --- derive port -> task map from setup_mcp_servers.ps1's $MCPS (single source of truth) ---
function Get-McpRegistry([string]$setupPath) {
  if (-not (Test-Path $setupPath)) { throw "setup_mcp_servers.ps1 not found at $setupPath" }
  $text = Get-Content -Raw -LiteralPath $setupPath
  $re = [regex]'name="(?<name>[^"]+)";\s*dir="(?<dir>[^"]+)";\s*port=(?<port>\d+);\s*task="(?<task>[^"]+)"'
  $out = @()
  foreach ($m in $re.Matches($text)) {
    $out += [pscustomobject]@{ name=$m.Groups['name'].Value; dir=$m.Groups['dir'].Value
                               port=[int]$m.Groups['port'].Value; task=$m.Groups['task'].Value }
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
$registry = Get-McpRegistry $SetupPath
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
