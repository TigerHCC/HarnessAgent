<#
.SYNOPSIS
  One-click setup of all HarnessAgent Windows diagnostic MCP servers on this machine.

.DESCRIPTION
  Installs Python dependencies, registers (and starts) an elevated logon Scheduled Task for each
  MCP server, and registers each extension into goose's config.yaml. Idempotent -- safe to re-run.

  Companion to setup_goose.ps1 (which installs goose itself + the base config). Run THAT first on a
  fresh machine, then this. Requires: an elevated PowerShell (Scheduled Tasks + the servers read
  SYSTEM-hive / kernel data), Python 3 on PATH with pip.

  The 8 servers (all loopback, read-only, streamable HTTP):
    srum 8777, eventlog 8778, crash 8779, exec 8780, drift 8781, netconn 8782, perfmon 8783, disk 8784

.PARAMETER SkipDeps      Skip `pip install` of Python dependencies.
.PARAMETER SkipTasks     Don't register Scheduled Tasks (just start the servers once, this session).
.PARAMETER NoStart       Register the tasks but don't start the servers now.
.PARAMETER SkipConfig    Don't touch goose's config.yaml.
.PARAMETER ConfigPath    Override goose config path (default %APPDATA%\Block\goose\config\config.yaml).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\setup_mcp_servers.ps1
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\setup_mcp_servers.ps1 -SkipConfig -NoStart
#>
[CmdletBinding()]
param(
  [switch]$SkipDeps,
  [switch]$SkipTasks,
  [switch]$NoStart,
  [switch]$SkipConfig,
  [string]$ConfigPath = (Join-Path $env:APPDATA "Block\goose\config\config.yaml")
)

$ErrorActionPreference = "Stop"
function Info($m){ Write-Host "[*] $m" -ForegroundColor Cyan }
function Ok($m){ Write-Host "[OK] $m" -ForegroundColor Green }
function Warn($m){ Write-Host "[!] $m" -ForegroundColor Yellow }
function Die($m){ Write-Host "[X] $m" -ForegroundColor Red; exit 1 }

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$mcpRoot = Join-Path $here "mcp"

# --- MCP registry: name, dir, port, scheduled-task name, config description ---
$MCPS = @(
  @{ name="srum";     dir="windows_srum";     port=8777; task="SRUM-MCP";
     desc="Windows SRUM + live system resource usage (CPU/mem/net/power) via local elevated MCP server (127.0.0.1:8777)" }
  @{ name="eventlog"; dir="windows_eventlog"; port=8778; task="EventLog-MCP";
     desc="Windows Event Log (system errors + user behavior) via local elevated MCP server (127.0.0.1:8778)" }
  @{ name="crash";    dir="windows_crash";    port=8779; task="Crash-MCP";
     desc="Windows crash/WER analysis (app crashes, hangs, BSOD bugchecks) via local elevated MCP server (127.0.0.1:8779)" }
  @{ name="exec";     dir="windows_exec";     port=8780; task="Exec-MCP";
     desc="Windows execution evidence (Prefetch/BAM/UserAssist/ShimCache + timeline) via local elevated MCP server (127.0.0.1:8780)" }
  @{ name="drift";    dir="windows_drift";    port=8781; task="Drift-MCP";
     desc="Windows config-drift (autoruns/services/programs/tasks snapshots + diff) via local elevated MCP server (127.0.0.1:8781)" }
  @{ name="netconn";  dir="windows_netconn";  port=8782; task="Netconn-MCP";
     desc="Windows live network connections + owning process/service + baseline diff via local elevated MCP server (127.0.0.1:8782)" }
  @{ name="perfmon";  dir="windows_perfmon";  port=8783; task="Perfmon-MCP";
     desc="Windows real-time performance counters (CPU/disk-latency/memory/pool via PDH) + baselines via local MCP server (127.0.0.1:8783)" }
  @{ name="disk";     dir="windows_disk";     port=8784; task="Disk-MCP";
     desc="Windows storage diagnostics (USN file-change journal + SMART health + volume state) via local elevated MCP server (127.0.0.1:8784)" }
  @{ name="procinspect"; dir="windows_procinspect"; port=8785; task="Procinspect-MCP";
     desc="Windows process inspection (who-locks-a-file, hang/deadlock wait chains, loaded modules, handle-leak view) via local MCP server (127.0.0.1:8785)" }
)

Write-Host "=== HarnessAgent MCP servers setup (8 diagnostic MCPs) ===" -ForegroundColor Magenta

# --- 0. Prereqs ---
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) { Die "Must run ELEVATED (Administrator): the servers read SYSTEM-hive/kernel data and register Scheduled Tasks. Re-open PowerShell as Administrator." }

$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { Die "Python 3 not found on PATH. Install Python 3 (with pip) and re-run." }
Ok "Elevated + Python: $py ($((& $py --version) -join ' '))"

# --- 1. Python dependencies (union across all MCPs) ---
if ($SkipDeps) { Warn "Skipping pip install (-SkipDeps)." }
else {
  Info "Installing Python dependencies (mcp, pywin32, psutil, dissect.esedb, wmi)..."
  $deps = @("mcp>=1.2","pywin32>=306","psutil>=5.9","dissect.esedb>=3.0","wmi>=1.5")
  & $py -m pip install --disable-pip-version-check @deps
  if ($LASTEXITCODE -ne 0) { Die "pip install failed (exit $LASTEXITCODE)." }
  Ok "Dependencies installed."
}

# --- 2. Register + start each MCP ---
foreach ($m in $MCPS) {
  $dir = Join-Path $mcpRoot $m.dir
  $server = Join-Path $dir ("{0}_mcp_server.py" -f ($m.dir -replace '^windows_',''))
  # server filename == <name>_mcp_server.py where <name> is the dir minus 'windows_'
  $server = Get-ChildItem $dir -Filter "*_mcp_server.py" -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $server) { Warn "$($m.name): no *_mcp_server.py in $dir -- skipping."; continue }
  $server = $server.FullName

  if (-not $SkipTasks) {
    Info "$($m.name): registering Scheduled Task '$($m.task)' (elevated, at logon)..."
    $action = New-ScheduledTaskAction -Execute $py -Argument "`"$server`"" -WorkingDirectory $dir
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -RunLevel Highest -LogonType Interactive
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    Register-ScheduledTask -TaskName $m.task -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
  }

  if (-not $NoStart) {
    # if the port is already listening, leave it; else start the server detached
    $listening = Get-NetTCPConnection -State Listen -LocalPort $m.port -ErrorAction SilentlyContinue
    if ($listening) { Ok "$($m.name): already listening on $($m.port)." }
    else {
      $env:PYTHONIOENCODING = "utf-8"
      Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-Command',"`$env:PYTHONIOENCODING='utf-8'; & '$py' '$server'"
      Ok "$($m.name): started -> http://127.0.0.1:$($m.port)/mcp"
    }
  }
}

# --- 3. Register extensions into goose config.yaml ---
if ($SkipConfig) { Warn "Skipping goose config update (-SkipConfig)." }
elseif (-not (Test-Path $ConfigPath)) {
  Warn "goose config not found at $ConfigPath. Run setup_goose.ps1 first, then re-run this with default config, OR add the extension blocks manually (see config/windows_config.yaml)."
}
else {
  $cfg = Get-Content $ConfigPath -Raw
  if ($cfg -notmatch "(?m)^\s*extensions\s*:") {
    Warn "No 'extensions:' section in $ConfigPath -- not modifying it. Add the blocks from config/windows_config.yaml manually."
  }
  else {
    Copy-Item $ConfigPath "$ConfigPath.bak-mcpsetup" -Force
    $added = @()
    foreach ($m in $MCPS) {
      # already present?  (a line like '  <name>:' under extensions)
      if ($cfg -match ("(?m)^\s{2}" + [regex]::Escape($m.name) + "\s*:")) { continue }
      $block = @"

  $($m.name):
    type: streamable_http
    bundled: false
    name: $($m.name)
    enabled: true
    uri: http://127.0.0.1:$($m.port)/mcp
    headers: {}
    env_keys: []
    timeout: 120
    description: $($m.desc)
"@
      Add-Content -Path $ConfigPath -Value $block -Encoding UTF8
      $added += $m.name
    }
    if ($added.Count) { Ok ("Added extensions to config: " + ($added -join ", ") + "  (backup: $ConfigPath.bak-mcpsetup)") }
    else { Ok "All 7 extensions already present in config -- no change." }
    # sanity: does it still parse as YAML? (best-effort via python)
    & $py -c "import sys,yaml; yaml.safe_load(open(sys.argv[1],encoding='utf-8')); print('config YAML OK')" $ConfigPath 2>&1 | Out-Host
  }
}

# --- 4. Health check ---
Start-Sleep -Seconds 4
Write-Host ""
Write-Host "=== MCP server status ===" -ForegroundColor Magenta
foreach ($m in $MCPS) {
  $up = Get-NetTCPConnection -State Listen -LocalPort $m.port -ErrorAction SilentlyContinue
  # Use schtasks.exe (reliable non-interactively) rather than the CIM Get-ScheduledTask cmdlet.
  if ($SkipTasks) { $tkState = "task:skipped" }
  else {
    $q = schtasks /query /tn $m.task /fo csv 2>$null | Select-Object -Skip 1
    $tkState = if ($q) { "task:Ready" } else { "task:MISSING" }
  }
  $portState = if ($up) { "port $($m.port):UP" } else { "port $($m.port):down" }
  $color = if ($up) { "Green" } else { "Yellow" }
  Write-Host ("  {0,-9} {1,-14} {2}" -f $m.name, $portState, $tkState) -ForegroundColor $color
}
Write-Host ""
Ok "Done. Verify discovery: have goose call each *_health tool, or (if goose_web runs) open its sidebar."
Warn "Sysmon is separate (kernel driver + EULA) -- install it yourself: see tools\sysmon\README.md."
