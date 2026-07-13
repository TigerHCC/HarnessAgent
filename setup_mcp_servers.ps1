<#
.SYNOPSIS
  One-click setup of all HarnessAgent Windows diagnostic MCP servers on this machine.

.DESCRIPTION
  Installs Python dependencies, registers (and starts) an elevated logon Scheduled Task for each
  MCP server, and registers each extension into goose's config.yaml. Idempotent -- safe to re-run.

  Companion to setup_goose.ps1 (which installs goose itself + the base config). Run THAT first on a
  fresh machine, then this. Requires: an elevated PowerShell (Scheduled Tasks + the servers read
  SYSTEM-hive / kernel data), Python 3 on PATH with pip.

  The 14 servers (all loopback, streamable HTTP). The first 12 are read-only diagnostic MCPs:
    srum 8777, eventlog 8778, crash 8779, exec 8780, drift 8781, netconn 8782, perfmon 8783,
    disk 8784, procinspect 8785, memstate 8786, filterstack 8787, winupdate 8788
  dtmsdk 8789 is the DTM Sample/SDK util MCP -- NOT read-only (transmits telemetry / changes DTP config;
  gated per-command). obsidian 8790 is the Obsidian vault MCP -- writes markdown notes (gated per-note)
  and is the only server that runs UNELEVATED (RunLevel Limited).
  This setup also installs Sysmon (kernel driver + audit config; -SkipSysmon to opt out) to feed eventlog.

  NOTE ON PRIVILEGE: this INSTALLER needs Administrator (registering a RunLevel-Highest Scheduled
  Task requires it). That is separate from what each SERVER needs at runtime -- most of the 12 have
  tools that work fine unelevated, and only some hard-require admin for specific data sources. See
  the privilege table in mcp\README.md. Goose itself always runs unelevated and reaches the servers
  over loopback HTTP.

.PARAMETER SkipDeps      Skip `pip install` of Python dependencies.
.PARAMETER SkipTasks     Don't register Scheduled Tasks (just start the servers once, this session).
.PARAMETER NoStart       Register the tasks but don't start the servers now.
.PARAMETER SkipConfig    Don't touch goose's config.yaml.
.PARAMETER ConfigPath    Override goose config path (default %APPDATA%\Block\goose\config\config.yaml).
.PARAMETER Uninstall     Remove everything this script installs: stop the servers, unregister the
                         Scheduled Tasks, and strip the extension blocks from goose's config.yaml
                         (backed up first). Leaves pip packages and Sysmon alone.
.PARAMETER SkipSysmon    Don't install/refresh Sysmon. By default this script installs the Microsoft
                         Sysinternals Sysmon kernel driver + audit config from tools\sysmon\Sysmon.zip
                         (a security-relevant change that ACCEPTS the Sysinternals EULA); it feeds the
                         eventlog MCP. Idempotent: if Sysmon is already installed, its config is
                         refreshed rather than reinstalled.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\setup_mcp_servers.ps1
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\setup_mcp_servers.ps1 -SkipConfig -NoStart
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\setup_mcp_servers.ps1 -Uninstall
#>
[CmdletBinding()]
param(
  [switch]$SkipDeps,
  [switch]$SkipTasks,
  [switch]$NoStart,
  [switch]$SkipConfig,
  [switch]$Uninstall,
  [switch]$SkipSysmon,
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
  @{ name="memstate";  dir="windows_memstate";  port=8786; task="Memstate-MCP";
     desc="Windows memory attribution (pool tags / physical-memory composition / kernel-pool leak hunt) via local elevated MCP server (127.0.0.1:8786)" }
  @{ name="filterstack"; dir="windows_filterstack"; port=8787; task="Filterstack-MCP";
     desc="Windows filter-stack map (filesystem minifilters + NDIS/Winsock network filters + altitude classification) via local elevated MCP server (127.0.0.1:8787)" }
  @{ name="winupdate";  dir="windows_winupdate";  port=8788; task="Winupdate-MCP";
     desc="Windows Update history + failure HRESULTs + pending-reboot state via local MCP server (127.0.0.1:8788)" }
  @{ name="dtmsdk";  dir="dtm_sdk";  port=8789; task="DtmSdk-MCP";
     desc="DTM Sample/SDK utilities (DTP client SDK CLI wrappers: instrumentation/analytics/transmission/DTM/platinum) via local elevated MCP server (127.0.0.1:8789). NOT read-only -- can transmit telemetry + change DTP config; gated by per-command confirmation." }
  @{ name="obsidian"; dir="windows_obsidian"; port=8790; task="Obsidian-MCP"; runlevel="Limited";
     desc="Obsidian vault access (read/search/link-graph/tags/frontmatter + gated create/update of markdown notes) via local MCP server (127.0.0.1:8790). Runs UNELEVATED; writes are per-note confirmation-gated." }
)

$mode = if ($Uninstall) { "UNINSTALL" } else { "setup" }
Write-Host "=== HarnessAgent MCP servers $mode (14: 12 diagnostic + dtmsdk + obsidian) ===" -ForegroundColor Magenta

# --- 0. Prereqs ---
# Admin is needed to register/unregister a RunLevel-Highest Scheduled Task. It is NOT a statement
# about what each server needs at runtime -- see the privilege table in mcp\README.md.
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) { Die "Must run ELEVATED (Administrator): registering the Scheduled Tasks requires it. Re-open PowerShell as Administrator." }

$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { Die "Python 3 not found on PATH. Install Python 3 (with pip) and re-run." }
Ok "Elevated + Python: $py ($((& $py --version) -join ' '))"

# --- Remove one extension's block from config.yaml (returns the new text, or $null if absent) ---
# Deletes the '  <id>:' key line at indent 2 plus its body (up to the next line with indent <= 2).
function Remove-ExtensionBlock([string[]]$lines, [string]$extId) {
  $keyIdx = -1; $inExt = $false
  for ($i = 0; $i -lt $lines.Count; $i++) {
    $s = $lines[$i].Trim()
    if ($s -eq '' -or $s.StartsWith('#')) { continue }
    $indent = $lines[$i].Length - $lines[$i].TrimStart(' ').Length
    if ($indent -eq 0) { $inExt = ($s -eq 'extensions:'); continue }
    if ($inExt -and $indent -eq 2 -and $s -eq "${extId}:") { $keyIdx = $i; break }
  }
  if ($keyIdx -lt 0) { return $null }
  $end = $lines.Count
  for ($j = $keyIdx + 1; $j -lt $lines.Count; $j++) {
    $s = $lines[$j].Trim()
    if ($s -eq '' -or $s.StartsWith('#')) { continue }
    $ind = $lines[$j].Length - $lines[$j].TrimStart(' ').Length
    if ($ind -le 2) { $end = $j; break }
  }
  $keep = New-Object System.Collections.Generic.List[string]
  for ($k = 0; $k -lt $lines.Count; $k++) { if ($k -lt $keyIdx -or $k -ge $end) { [void]$keep.Add($lines[$k]) } }
  return ,$keep.ToArray()
}

# --- UNINSTALL: stop servers, drop tasks, strip config blocks, then exit ---
if ($Uninstall) {
  foreach ($m in $MCPS) {
    # stop whatever holds the port (that bind IS the server's single-instance lock)
    $conn = Get-NetTCPConnection -State Listen -LocalPort $m.port -ErrorAction SilentlyContinue
    foreach ($c in $conn) {
      try { Stop-Process -Id $c.OwningProcess -Force -ErrorAction Stop; Ok "$($m.name): stopped PID $($c.OwningProcess) (port $($m.port))." }
      catch { Warn "$($m.name): could not stop PID $($c.OwningProcess): $_" }
    }
    if (-not $conn) { Info "$($m.name): not running (port $($m.port) free)." }

    $q = schtasks /query /tn $m.task /fo csv 2>$null | Select-Object -Skip 1
    if ($q) {
      Unregister-ScheduledTask -TaskName $m.task -Confirm:$false -ErrorAction SilentlyContinue
      Ok "$($m.name): unregistered Scheduled Task '$($m.task)'."
    } else { Info "$($m.name): no Scheduled Task '$($m.task)'." }
  }

  if ($SkipConfig) { Warn "Leaving goose config alone (-SkipConfig)." }
  elseif (-not (Test-Path $ConfigPath)) { Warn "goose config not found at $ConfigPath -- nothing to strip." }
  else {
    Copy-Item $ConfigPath "$ConfigPath.bak-mcpuninstall" -Force
    $lines = [System.IO.File]::ReadAllLines($ConfigPath)
    $removed = @()
    foreach ($m in $MCPS) {
      $next = Remove-ExtensionBlock $lines $m.name
      if ($null -ne $next) { $lines = $next; $removed += $m.name }
    }
    if ($removed.Count) {
      [System.IO.File]::WriteAllLines($ConfigPath, $lines, (New-Object System.Text.UTF8Encoding($false)))
      Ok ("Removed extensions from config: " + ($removed -join ", ") + "  (backup: $ConfigPath.bak-mcpuninstall)")
      & $py -c "import sys,yaml; yaml.safe_load(open(sys.argv[1],encoding='utf-8')); print('config YAML OK')" $ConfigPath 2>&1 | Out-Host
    } else { Ok "No managed extensions present in config -- no change." }
  }
  Write-Host ""
  Ok "Uninstall done. Python packages were NOT removed (other things may use them)."
  Warn "goose_web and Sysmon are separate -- this script did not touch them."
  exit 0
}

# --- 1. Python dependencies (union of every mcp\windows_*\requirements.txt) ---
# Read from the requirements files rather than a hardcoded list, so adding a dep to any one MCP
# can't silently go uninstalled here.
if ($SkipDeps) { Warn "Skipping pip install (-SkipDeps)." }
else {
  $deps = @()
  foreach ($m in $MCPS) {
    $req = Join-Path (Join-Path $mcpRoot $m.dir) "requirements.txt"
    if (-not (Test-Path $req)) { Warn "$($m.name): no requirements.txt -- skipping its deps."; continue }
    foreach ($line in (Get-Content $req)) {
      $l = $line.Trim()
      if ($l -and -not $l.StartsWith('#')) { $deps += $l }
    }
  }
  $deps = @($deps | Sort-Object -Unique)
  if (-not $deps.Count) { Die "No dependencies found in any mcp\windows_*\requirements.txt -- is the checkout complete?" }
  Info ("Installing Python dependencies (" + ($deps -join ", ") + ")...")
  & $py -m pip install --disable-pip-version-check @deps
  if ($LASTEXITCODE -ne 0) { Die "pip install failed (exit $LASTEXITCODE)." }
  Ok "Dependencies installed."
}

# --- 2. Register + start each MCP ---
foreach ($m in $MCPS) {
  $dir = Join-Path $mcpRoot $m.dir
  # server filename == <name>_mcp_server.py where <name> is the dir minus 'windows_'
  $server = Get-ChildItem $dir -Filter "*_mcp_server.py" -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $server) { Warn "$($m.name): no *_mcp_server.py in $dir -- skipping."; continue }
  $server = $server.FullName

  if (-not $SkipTasks) {
    Info "$($m.name): registering Scheduled Task '$($m.task)' (elevated, at logon)..."
    $action = New-ScheduledTaskAction -Execute $py -Argument "`"$server`"" -WorkingDirectory $dir
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $rl = if ($m.runlevel) { $m.runlevel } else { "Highest" }
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -RunLevel $rl -LogonType Interactive
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
      # YAML-single-quote the description: a plain scalar cannot contain ": " (colon+space) -- it would
      # be read as a nested mapping. Single-quoting allows colons/semicolons/etc.; embedded single
      # quotes are escaped by doubling.
      $descYaml = "'" + ($m.desc -replace "'", "''") + "'"
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
    description: $descYaml
"@
      Add-Content -Path $ConfigPath -Value $block -Encoding UTF8
      $added += $m.name
    }
    if ($added.Count) { Ok ("Added extensions to config: " + ($added -join ", ") + "  (backup: $ConfigPath.bak-mcpsetup)") }
    else { Ok ("All $($MCPS.Count) extensions already present in config -- no change.") }
    # sanity: does it still parse as YAML? (best-effort via python)
    & $py -c "import sys,yaml; yaml.safe_load(open(sys.argv[1],encoding='utf-8')); print('config YAML OK')" $ConfigPath 2>&1 | Out-Host
  }
}

# --- 3.5 Sysmon (feeds the eventlog MCP; not an MCP itself) ------------------
# Installs the Microsoft Sysinternals Sysmon kernel driver + low-noise audit config from the committed
# tools\sysmon\Sysmon.zip, so the eventlog MCP can query Microsoft-Windows-Sysmon/Operational. This is a
# security-relevant system change and ACCEPTS the Sysinternals EULA (tools\sysmon\Eula.txt) via
# -accepteula -- pass -SkipSysmon to opt out. Idempotent: if Sysmon is already installed, its config is
# refreshed (-c) instead of reinstalling (which would error).
if ($SkipSysmon) { Warn "Skipping Sysmon (-SkipSysmon). Install manually later: see tools\sysmon\README.md." }
else {
  $sysmonDir = Join-Path $here "tools\sysmon"
  $sysmonCfg = Join-Path $sysmonDir "sysmon-config.xml"
  # Pick the binary this machine can run (ARM64 has its own build; x64 vs x86 otherwise). Use the
  # MACHINE arch, not the process arch: a 32-bit or x64-emulated PowerShell host reports the wrong value
  # in PROCESSOR_ARCHITECTURE. PROCESSOR_ARCHITEW6432 holds the true arch under WOW64/emulation.
  $machineArch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
  $exeName = switch ($machineArch) {
    "ARM64" { "Sysmon64a.exe" } "AMD64" { "Sysmon64.exe" } default { "Sysmon.exe" }
  }
  $sysmonExe = Join-Path $sysmonDir $exeName
  if (-not (Test-Path $sysmonExe)) {
    $zip = Join-Path $sysmonDir "Sysmon.zip"
    if (Test-Path $zip) {
      Info "Extracting $exeName from Sysmon.zip..."
      try { Expand-Archive -Path $zip -DestinationPath $sysmonDir -Force } catch { Warn "Sysmon.zip extract failed: $_" }
    }
  }
  if (-not (Test-Path $sysmonExe)) {
    Warn "$exeName not found and Sysmon.zip missing -- skipping Sysmon. See tools\sysmon\README.md."
  }
  elseif (-not (Test-Path $sysmonCfg)) {
    Warn "sysmon-config.xml not found -- skipping Sysmon."
  }
  else {
    # Match ANY Sysmon service name (Sysmon / Sysmon64 / Sysmon64a on ARM64 / a custom -i name) so an
    # already-installed Sysmon always takes the -c refresh path, never an erroring -i reinstall.
    $svc = Get-Service -Name "Sysmon*" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($svc) {
      Info "Sysmon already installed ($($svc.Name)) -- refreshing audit config (no reinstall)..."
      # -accepteula on -c too: if the current user never accepted the EULA (Sysmon installed by another
      # admin/SCCM), a bare -c can pop an interactive EULA dialog and hang this non-interactive script.
      & $sysmonExe -accepteula -c $sysmonCfg | Out-Host
      if ($LASTEXITCODE -eq 0) { Ok "Sysmon config refreshed from sysmon-config.xml." }
      else { Warn "Sysmon -c returned exit $LASTEXITCODE (see tools\sysmon\README.md)." }
    }
    else {
      Warn "Installing Sysmon (Microsoft kernel driver + audit config). This ACCEPTS the Sysinternals EULA (tools\sysmon\Eula.txt)."
      & $sysmonExe -accepteula -i $sysmonCfg | Out-Host
      if ($LASTEXITCODE -eq 0) { Ok "Sysmon installed -> query it via the eventlog MCP (channel Microsoft-Windows-Sysmon/Operational)." }
      else { Warn "Sysmon install returned exit $LASTEXITCODE (see tools\sysmon\README.md)." }
    }
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
if (-not $SkipSysmon) { Ok "Sysmon step ran (see above). Query it via the eventlog MCP: Microsoft-Windows-Sysmon/Operational." }
