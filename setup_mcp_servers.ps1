<#
.SYNOPSIS
  One-click setup of all HarnessAgent Windows diagnostic MCP servers on this machine.

.DESCRIPTION
  Installs Python dependencies, registers (and starts) an elevated logon Scheduled Task for each
  MCP server, and registers each extension into goose's config.yaml. Idempotent -- safe to re-run.
  Also installs (section 3.7, unless -SkipExtras) the manifest-external services that live outside
  config\mcp_servers.json: markitdown (8794), docstruct (8795), and the goose_web browser UI as the
  'GooseWeb' scheduled task -- so this one script sets up the whole stack.

  Companion to setup_goose.ps1 (which installs goose itself + the base config). Run THAT first on a
  fresh machine, then this. Requires: an elevated PowerShell (Scheduled Tasks + the servers read
  SYSTEM-hive / kernel data), Python 3 on PATH with pip.

  The 17 servers (all loopback, streamable HTTP). The first 12 are read-only diagnostic MCPs:
    srum 8777, eventlog 8778, crash 8779, exec 8780, drift 8781, netconn 8782, perfmon 8783,
    disk 8784, procinspect 8785, memstate 8786, filterstack 8787, winupdate 8788
  dtmsdk 8789 is the DTM Sample/SDK util MCP -- NOT read-only (transmits telemetry / changes DTP config;
  gated per-command). obsidian 8790 is the Obsidian vault MCP -- writes markdown notes (gated per-note).
  dtm_download 8791 downloads DTP builds from Artifactory (writes only into its own download_path).
  dtm_deploy 8792 wraps DTP uninstall/install/consent/plugin/transmission (gated per-tool). scheduler 8793
  fires headless `goose run` agent tasks on cron/at schedules (confirm-token gated mutating tools + direct
  read tools). obsidian, dtm_download, and scheduler are the only three servers that run UNELEVATED
  (RunLevel Limited).
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
.PARAMETER SkipWatchdog  Don't install the MCP watchdog. By default this registers tools\mcp_watchdog
                         (a Scheduled Task that every 5 min restarts any MCP server whose event loop has
                         wedged -- listening but not answering -- which would otherwise hang Goose).

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
  [switch]$SkipWatchdog,
  [switch]$SkipExtras,
  [string]$ConfigPath = (Join-Path $env:APPDATA "Block\goose\config\config.yaml")
)

$ErrorActionPreference = "Stop"
function Info($m){ Write-Host "[*] $m" -ForegroundColor Cyan }
function Ok($m){ Write-Host "[OK] $m" -ForegroundColor Green }
function Warn($m){ Write-Host "[!] $m" -ForegroundColor Yellow }
function Die($m){ Write-Host "[X] $m" -ForegroundColor Red; exit 1 }

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$mcpRoot = Join-Path $here "mcp"
$launcher = Join-Path $here "scripts\start_mcp_hidden.ps1"

# --- MCP registry: name, dir, port, scheduled-task name, config description ---
$manifestPath = Join-Path $here "config\mcp_servers.json"
if (-not (Test-Path -LiteralPath $manifestPath)) { Die "MCP manifest not found: $manifestPath" }
try {
  $decodedManifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
  $manifestEntries = @($decodedManifest | ForEach-Object { $_ })
}
catch { Die "Invalid MCP manifest JSON: $_" }
if ($manifestEntries.Count -ne 17) { Die "MCP manifest must contain exactly 17 entries on canonical ports 8777-8793; found $($manifestEntries.Count) entries." }

$MCPS = New-Object System.Collections.ArrayList
$seenNames = @{}
$seenPorts = @{}
$seenTasks = @{}
$textFields = @("name","directory","task","run_level","description","health_tool")
$integerTypes = @([byte],[sbyte],[int16],[uint16],[int32],[uint32],[int64],[uint64])
$expectedPorts = @(8777..8793)
foreach ($entry in $manifestEntries) {
  foreach ($field in @("name","directory","port","task","run_level","description","health_tool")) {
    if ($null -eq $entry.$field) {
      Die "MCP manifest entry is missing '$field'."
    }
  }
  foreach ($field in $textFields) {
    if (-not ($entry.$field -is [string]) -or [string]::IsNullOrWhiteSpace($entry.$field)) {
      Die "MCP manifest entry has invalid '$field'; expected a non-empty string."
    }
  }
  if ($integerTypes -notcontains $entry.port.GetType()) {
    Die "MCP manifest entry has invalid 'port'; expected an integer."
  }
  if ($entry.run_level -notin @("Highest","Limited")) { Die "Invalid run_level for $($entry.name): $($entry.run_level)" }
  if ($entry.port -notin $expectedPorts) {
    Die "MCP manifest must use canonical ports 8777-8793 exactly once."
  }
  if ($seenNames.ContainsKey($entry.name)) { Die "MCP manifest contains duplicate name: $($entry.name)" }
  if ($seenPorts.ContainsKey($entry.port)) { Die "MCP manifest contains duplicate port: $($entry.port)" }
  if ($seenTasks.ContainsKey($entry.task)) { Die "MCP manifest contains duplicate task: $($entry.task)" }
  $seenNames[$entry.name] = $true
  $seenPorts[$entry.port] = $true
  $seenTasks[$entry.task] = $true
  [void]$MCPS.Add(@{ name=[string]$entry.name; dir=[string]$entry.directory; port=[int]$entry.port;
    task=[string]$entry.task; runlevel=[string]$entry.run_level; desc=[string]$entry.description;
    health_tool=[string]$entry.health_tool })
}
$actualPorts = @($seenPorts.Keys | ForEach-Object { [int]$_ } | Sort-Object)
if (@(Compare-Object -ReferenceObject $expectedPorts -DifferenceObject $actualPorts).Count) {
  Die "MCP manifest must use canonical ports 8777-8793 exactly once."
}

. (Join-Path $here "scripts\mcp_task_helpers.ps1")
$powershell = (Get-Command powershell -ErrorAction SilentlyContinue).Source
if (-not $powershell) { Die "Windows PowerShell not found on PATH." }
$logRoot = Join-Path $here "logs\mcp"

$mode = if ($Uninstall) { "UNINSTALL" } else { "setup" }
Write-Host "=== HarnessAgent MCP servers $mode (17: 12 diagnostic + dtmsdk + obsidian + dtm_download + dtm_deploy + scheduler) ===" -ForegroundColor Magenta

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
  # remove the MCP watchdog too (it only makes sense alongside the servers)
  $wdUninstall = Join-Path $here "tools\mcp_watchdog\uninstall_watchdog.ps1"
  if (Test-Path $wdUninstall) { try { & $wdUninstall | Out-Host } catch {} }
  # remove the manifest-external services + goose_web (symmetry with section 3.7)
  if (-not $SkipExtras) {
    foreach ($t in 'MarkItDown-MCP', 'DocStruct-MCP', 'GooseWeb') {
      $q = schtasks /query /tn $t /fo csv 2>$null | Select-Object -Skip 1
      if ($q) {
        Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $t -Confirm:$false -ErrorAction SilentlyContinue
        Ok "Unregistered Scheduled Task '$t'."
      }
    }
    if (-not $SkipConfig -and (Test-Path $ConfigPath)) {
      $lines2 = [System.IO.File]::ReadAllLines($ConfigPath); $rm2 = @()
      foreach ($nm in 'markitdown', 'docstruct') { $nx = Remove-ExtensionBlock $lines2 $nm; if ($null -ne $nx) { $lines2 = $nx; $rm2 += $nm } }
      if ($rm2.Count) { [System.IO.File]::WriteAllLines($ConfigPath, $lines2, (New-Object System.Text.UTF8Encoding($false))); Ok ("Removed extra extensions: " + ($rm2 -join ", ")) }
    }
  }
  Write-Host ""
  Ok "Uninstall done. Python packages were NOT removed (other things may use them)."
  Warn "Sysmon is separate -- this script did not touch it (uninstall via tools\sysmon)."
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
    $action = New-McpScheduledTaskAction -PowerShellPath $powershell -LauncherPath $launcher `
      -PythonPath $py -ServerPath $server -WorkingDirectory $dir -Name $m.name -LogDirectory $logRoot
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
      Start-McpHiddenServer -PowerShellPath $powershell -LauncherPath $launcher `
        -PythonPath $py -ServerPath $server -WorkingDirectory $dir -Name $m.name -LogDirectory $logRoot
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

# --- 3.6 MCP watchdog ---------------------------------------------------------
# Registers a Scheduled Task that every 5 min restarts any MCP server whose event loop has wedged
# (listening but not answering) -- which would otherwise hang Goose. See tools\mcp_watchdog\README.md.
if ($SkipWatchdog) { Warn "Skipping MCP watchdog (-SkipWatchdog)." }
elseif ($SkipTasks) { Warn "Skipping Scheduled Task registration (-SkipTasks)." }
else {
  $wdInstall = Join-Path $here "tools\mcp_watchdog\install_watchdog.ps1"
  if (Test-Path $wdInstall) {
    Info "Installing the MCP watchdog (restarts a wedged MCP every 5 min)..."
    try { & $wdInstall | Out-Host } catch { Warn "watchdog install failed: $_" }
  } else { Warn "tools\mcp_watchdog\install_watchdog.ps1 not found -- skipping watchdog." }
}

# --- 3.7 Manifest-external services (markitdown 8794, docstruct 8795) + goose_web UI ---------
# These live OUTSIDE config/mcp_servers.json on purpose: markitdown/docstruct have no health_tool
# (so the watchdog/batch-test don't manage them) and run on 8794/8795; goose_web is the browser UI,
# not an MCP. Installing them here makes this script the single install entry point for the whole
# stack. Each sub-installer is idempotent (safe to re-run). Skip with -SkipExtras.
if ($SkipExtras) { Warn "Skipping manifest-external services + goose_web (-SkipExtras)." }
else {
  $pyx = (Get-Command python -ErrorAction SilentlyContinue).Source
  foreach ($x in @(
      @{ name = 'markitdown'; dir = 'mcp\markitdown'; task = 'MarkItDown-MCP' },
      @{ name = 'docstruct';  dir = 'mcp\docstruct';  task = 'DocStruct-MCP' })) {
    $xdir = Join-Path $here $x.dir
    if (-not $SkipDeps -and $pyx) {
      $req = Join-Path $xdir 'requirements.txt'
      if (Test-Path $req) { Info "$($x.name): installing deps..."; & $pyx -m pip install -q -r $req 2>&1 | Out-Null }
    }
    if (-not $SkipTasks) {
      $inst = Join-Path $xdir 'install_task.ps1'
      if (Test-Path $inst) { try { & $inst | Out-Null; Ok "$($x.name): scheduled task '$($x.task)' registered." } catch { Warn "$($x.name) install_task failed: $_" } }
    }
    if (-not $SkipConfig) {
      $reg = Join-Path $xdir 'register_goose_extension.ps1'
      if (Test-Path $reg) { try { & $reg -ConfigPath $ConfigPath | Out-Null; Ok "$($x.name): extension registered in goose config." } catch { Warn "$($x.name) register failed: $_" } }
    }
    if (-not $NoStart -and -not $SkipTasks) { Start-ScheduledTask -TaskName $x.task -ErrorAction SilentlyContinue }
  }
  # goose_web browser UI as the 'GooseWeb' scheduled task (RunLevel Highest; binds :8799 all interfaces)
  if ($SkipTasks) { Warn "goose_web: skipping task registration (-SkipTasks)." }
  else {
    $gwInstall = Join-Path $here 'goose_web\install_web_task.ps1'
    if (Test-Path $gwInstall) {
      try {
        & $gwInstall | Out-Null; Ok "goose_web: scheduled task 'GooseWeb' registered."
        if (-not $NoStart) { Start-ScheduledTask -TaskName GooseWeb -ErrorAction SilentlyContinue }
      } catch { Warn "goose_web install_web_task failed: $_" }
    } else { Warn "goose_web\install_web_task.ps1 not found -- skipping goose_web." }
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
