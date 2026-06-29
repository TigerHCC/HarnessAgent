#Requires -Version 5.1
<#
  Goose Harness Web (PowerShell port of server.py) -- a thin HTTP bridge that
  drives `goose run` and streams to a browser. Stdlib only (.NET / HttpListener),
  works on Windows PowerShell 5.1 and PowerShell 7+.

  Endpoints (identical contract to server.py / index.html):
    GET  /                the web UI (index.html, same directory)
    GET  /api/health      JSON: model + backend status + tool list + version
    POST /api/chat        streams NDJSON events; body {"session","message","mode"}
                          events: {type:text|tool_start|tool_args|done|error,...}

  Each chat turn runs:  goose run -n <session> [-r] --max-turns N -i -
  with the user message fed on STDIN (avoids all Windows command-line quoting),
  cwd = workspace, and GOOSE_MODE set per-process ("auto" runs tools, "chat" is
  model-only). First turn for a session omits -r; later turns add -r to resume.

  Configuration: config.json next to this file (shared with server.py). Any
  GOOSE_WEB_* environment variable overrides the matching value. See README.

  Windows note: binding 0.0.0.0 (all interfaces) with HttpListener needs either
  an elevated shell OR a one-time URL ACL reservation, e.g.:
     netsh http add urlacl url=http://+:8799/ user=%USERNAME%
  Binding 127.0.0.1 needs neither. serve_web.ps1 wraps the common cases.

  Stop with Ctrl+C.
#>
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$Here = $PSScriptRoot
if (-not $Here) { $Here = Split-Path -Parent $MyInvocation.MyCommand.Path }

# ----------------------------------------------------------------------------
# config: defaults <- config.json <- GOOSE_WEB_* env (env wins)
# ----------------------------------------------------------------------------
function Load-WebConfig {
    $defaults = @{
        host            = '0.0.0.0'
        port            = 8799
        token           = ''
        workspace       = (Join-Path $Here '..\workspace')
        max_turns       = 50
        timeout_seconds = 1800
        goose_bin       = ''
        model           = 'qwen-3.6-chat'
        provider_label  = 'vLLM (OpenAI-compat)'
        backends        = @(
            @{ name = 'vLLM chat';  url = 'http://192.168.86.44:8000';  health_path = '/v1/models'; role = 'chat'  }
            @{ name = 'vLLM embed'; url = 'http://192.168.86.44:8001';  health_path = '/v1/models'; role = 'embed' }
            @{ name = 'Ollama';     url = 'http://192.168.86.44:11434'; health_path = '/api/tags';  role = 'ollama' }
        )
    }
    $cfg = @{}; foreach ($k in $defaults.Keys) { $cfg[$k] = $defaults[$k] }

    $path = if ($env:GOOSE_WEB_CONFIG) { $env:GOOSE_WEB_CONFIG } else { Join-Path $Here 'config.json' }
    if (Test-Path $path) {
        try {
            $loaded = Get-Content -Raw -Encoding UTF8 -LiteralPath $path | ConvertFrom-Json
            foreach ($p in $loaded.PSObject.Properties) {
                if ($p.Name -notlike '_*') { $cfg[$p.Name] = $p.Value }
            }
        } catch { Write-Warning "[goose_web] could not parse $path : $_ ; using defaults/env" }
    }

    if ($env:GOOSE_WEB_HOST)              { $cfg.host = $env:GOOSE_WEB_HOST }
    if ($env:GOOSE_WEB_PORT)              { $cfg.port = [int]$env:GOOSE_WEB_PORT }
    if ($null -ne $env:GOOSE_WEB_TOKEN)   { $cfg.token = $env:GOOSE_WEB_TOKEN }
    if ($env:GOOSE_WEB_WORKSPACE)         { $cfg.workspace = $env:GOOSE_WEB_WORKSPACE }
    if ($env:GOOSE_WEB_MAXTURNS)          { $cfg.max_turns = [int]$env:GOOSE_WEB_MAXTURNS }
    if ($env:GOOSE_WEB_TIMEOUT)           { $cfg.timeout_seconds = [int]$env:GOOSE_WEB_TIMEOUT }
    if ($env:GOOSE_WEB_MODEL)             { $cfg.model = $env:GOOSE_WEB_MODEL }
    if ($env:GOOSE_BIN)                   { $cfg.goose_bin = $env:GOOSE_BIN }
    return $cfg
}

$CFG        = Load-WebConfig
$HostAddr   = [string]$CFG.host
$Port       = [int]$CFG.port
$Token      = ([string]$CFG.token).Trim()
$MaxTurns   = [int]$CFG.max_turns
$TimeoutSec = [int]$CFG.timeout_seconds

# resolve workspace to an absolute path and make sure it exists
try { $Workspace = [System.IO.Path]::GetFullPath((Join-Path $Here ([string]$CFG.workspace))) }
catch { $Workspace = [string]$CFG.workspace }
if (-not (Test-Path -LiteralPath $Workspace)) { New-Item -ItemType Directory -Force -Path $Workspace | Out-Null }

# ----------------------------------------------------------------------------
# goose.exe discovery
# ----------------------------------------------------------------------------
function Find-Goose($cfg) {
    $cands = @()
    if ($cfg.goose_bin) { $cands += [string]$cfg.goose_bin }
    if ($HOME)            { $cands += (Join-Path $HOME '.local\bin\goose.exe') }
    if ($env:USERPROFILE) { $cands += (Join-Path $env:USERPROFILE '.local\bin\goose.exe') }
    foreach ($c in $cands) { if ($c -and (Test-Path -LiteralPath $c)) { return (Resolve-Path -LiteralPath $c).Path } }
    foreach ($n in 'goose', 'goose.exe') {
        $g = Get-Command $n -ErrorAction SilentlyContinue
        if ($g) { return $g.Source }
    }
    return 'goose'
}
$GooseBin = Find-Goose $CFG

function Get-GooseVersion($bin) {
    try { $out = & $bin --version 2>&1 | Out-String; return (($out.Trim() -split "`n")[0]).Trim() }
    catch { return 'unknown' }
}
$GooseVersion = Get-GooseVersion $GooseBin
$IndexPath = Join-Path $Here 'index.html'

# ----------------------------------------------------------------------------
# shared, thread-safe state (one set of seen sessions + per-session lock objects)
# ----------------------------------------------------------------------------
$Shared = [hashtable]::Synchronized(@{ seen = @{}; locks = @{} })

# ----------------------------------------------------------------------------
# compiled regexes -- built from code points so file encoding can't corrupt the
# non-ASCII markers (these objects are thread-safe for matching, shared by all workers)
# ----------------------------------------------------------------------------
$ESC = [char]27; $BEL = [char]7
$BOX1 = [char]0x2500; $BOX2 = [char]0x2501; $EMD = [char]0x2014   # box drawing / em dash
$CHEV = [char]0x25B8; $BULLET = [char]0x25CF                      # tool arrow / session bullet
$reAnsi   = [regex]($ESC + '\[[0-9;?]*[ -/]*[@-~]|' + $ESC + '\][^' + $BEL + ']*' + $BEL)
$reMascot = [regex]('__\(|\\____\)|^\s*L L\s|goose is ready|' + $BULLET + ' (?:new session|resuming)')
$reRule   = [regex]('^[\s' + $BOX1 + $BOX2 + $EMD + '_-]*$')
$reTool   = [regex]('^\s*' + $CHEV + '\s+(.+?)\s*$')

# ----------------------------------------------------------------------------
# HttpListener
# ----------------------------------------------------------------------------
$prefHost = if ($HostAddr -in '0.0.0.0', '*', '+', '::') { '+' } else { $HostAddr }
$prefix   = "http://$prefHost`:$Port/"
$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add($prefix)
try {
    $listener.Start()
} catch [System.Net.HttpListenerException] {
    Write-Host ""
    Write-Host "  [!] Could not bind $prefix : $($_.Exception.Message)" -ForegroundColor Red
    if ($prefHost -eq '+') {
        Write-Host "      Binding all interfaces needs admin OR a one-time URL ACL. Either:" -ForegroundColor Yellow
        Write-Host "        - run this shell as Administrator, or" -ForegroundColor Yellow
        Write-Host "        - reserve the URL once (elevated):" -ForegroundColor Yellow
        Write-Host "            netsh http add urlacl url=$prefix user=$env:USERNAME" -ForegroundColor Yellow
        Write-Host "        - or set GOOSE_WEB_HOST=127.0.0.1 for local-only (no admin needed)." -ForegroundColor Yellow
    }
    exit 1
}

$bindPublic = $prefHost -ne '127.0.0.1'
Write-Host ("=" * 64)
Write-Host "  Goose Harness Web (PowerShell)  ->  $prefix"
Write-Host "  goose     : $GooseVersion  ($GooseBin)"
Write-Host "  model     : $($CFG.model)"
Write-Host "  workspace : $Workspace"
Write-Host "  token     : $(if ($Token) { 'required' } else { 'NONE' })"
Write-Host ("=" * 64)
if ($bindPublic -and -not $Token) {
    Write-Host "  [!] SECURITY: bound to a public interface with GOOSE_MODE=auto and no" -ForegroundColor Yellow
    Write-Host "      token. Anyone who can reach this port can run shell commands on this" -ForegroundColor Yellow
    Write-Host "      box via the agent. Set GOOSE_WEB_TOKEN=<secret>, or use 127.0.0.1." -ForegroundColor Yellow
    Write-Host ("=" * 64)
}
Write-Host "  serving... (Ctrl+C to stop)"

# state bundle handed to every worker runspace (live objects shared by reference)
$S = @{
    cfg = $CFG; gooseBin = $GooseBin; indexPath = $IndexPath; maxTurns = $MaxTurns
    timeoutSec = $TimeoutSec; token = $Token; workspace = $Workspace; gooseVer = $GooseVersion
    shared = $Shared; reAnsi = $reAnsi; reMascot = $reMascot; reRule = $reRule; reTool = $reTool
}

# ----------------------------------------------------------------------------
# worker: self-contained (all helpers + accept loop). N copies run concurrently
# so a long chat stream does not block the 20s health polls.
# ----------------------------------------------------------------------------
$worker = {
    param($listener, $S)

    $UTF8 = New-Object System.Text.UTF8Encoding($false)

    function Test-Url($url, $timeoutSec = 4) {
        try {
            $req = [System.Net.HttpWebRequest]::Create($url)
            $req.Method = 'GET'; $req.Timeout = [int]($timeoutSec * 1000); $req.ReadWriteTimeout = [int]($timeoutSec * 1000)
            $resp = $req.GetResponse(); $code = [int]$resp.StatusCode; $resp.Close()
            return ($code -ge 200 -and $code -lt 500)
        } catch [System.Net.WebException] {
            $r = $_.Exception.Response
            if ($r) { try { $c = [int]$r.StatusCode; $r.Close(); return ($c -ge 200 -and $c -lt 500) } catch {} }
            return $false
        } catch { return $false }
    }

    function Get-ChatUrl($cfg) {
        foreach ($b in $cfg.backends) { if ($b.role -eq 'chat') { return ([string]$b.url).TrimEnd('/') } }
        if ($cfg.backends.Count -gt 0) { return ([string]$cfg.backends[0].url).TrimEnd('/') }
        return ''
    }

    function Build-Health($S) {
        $cfg = $S.cfg
        $backends = @()
        foreach ($b in $cfg.backends) {
            $u = ([string]$b.url).TrimEnd('/')
            $hp = if ($b.health_path) { [string]$b.health_path } else { '/' }
            $backends += @{ name = [string]$b.name; detail = $u; ok = (Test-Url ($u + $hp)) }
        }
        $tools = @(
            @{ group = 'developer'; name = 'shell / write / edit';  desc = 'run commands, read & edit files' }
            @{ group = 'memory';    name = 'remember / retrieve';   desc = 'persistent key/value memory (MCP)' }
            @{ group = 'dtm';       name = 'dtm_query';             desc = 'auto-route a DTM question' }
            @{ group = 'dtm';       name = 'dtm_telemetry_lookup';  desc = 'datatypes/fields/plugins for a data need' }
            @{ group = 'dtm';       name = 'dtm_triage';            desc = 'triage a Windows issue from telemetry + Jira history' }
            @{ group = 'dtm';       name = 'dtm_data_feature';      desc = 'deep-dive a DTM plugin' }
            @{ group = 'dtm';       name = 'dtm_hw_spec';           desc = 'hardware / platform spec lookup' }
            @{ group = 'dtm';       name = 'dtm_health';            desc = 'DTM agent health' }
            @{ group = 'pk';        name = 'search_kb';             desc = 'semantic search over the personal KB' }
            @{ group = 'pk';        name = 'get_document';          desc = 'fetch the full text of a KB markdown file' }
            @{ group = 'pk';        name = 'list_sources';          desc = 'per-source chunk counts in the KB' }
        )
        return @{
            ok = $true; version = $S.gooseVer; model = [string]$cfg.model
            provider = ([string]$cfg.provider_label + ' @ ' + (Get-ChatUrl $cfg))
            workspace = [string]$S.workspace; token_required = [bool]$S.token
            backends = $backends; tools = $tools
        }
    }

    function Send-Bytes($ctx, $bytes, $ctype, $status = 200) {
        $ctx.Response.StatusCode = $status
        $ctx.Response.ContentType = $ctype
        try { $ctx.Response.Headers['Cache-Control'] = 'no-store' } catch {}
        $ctx.Response.ContentLength64 = $bytes.Length
        $ctx.Response.OutputStream.Write($bytes, 0, $bytes.Length)
        $ctx.Response.OutputStream.Close()
    }
    function Send-Json($ctx, $obj, $status = 200) {
        $json = ($obj | ConvertTo-Json -Depth 8 -Compress)
        Send-Bytes $ctx ($UTF8.GetBytes($json)) 'application/json; charset=utf-8' $status
    }
    function Send-File($ctx, $path, $ctype) {
        if (-not (Test-Path -LiteralPath $path)) { Send-Json $ctx @{ error = 'not found' } 404; return }
        Send-Bytes $ctx ([System.IO.File]::ReadAllBytes($path)) $ctype 200
    }

    function Emit($out, $obj) {
        try {
            $line = ($obj | ConvertTo-Json -Depth 8 -Compress) + "`n"
            $b = $UTF8.GetBytes($line); $out.Write($b, 0, $b.Length); $out.Flush(); return $true
        } catch { return $false }
    }

    function Get-SessionLock($shared, $name) {
        [System.Threading.Monitor]::Enter($shared.SyncRoot)
        try {
            if (-not $shared.locks.ContainsKey($name)) { $shared.locks[$name] = (New-Object object) }
            return $shared.locks[$name]
        } finally { [System.Threading.Monitor]::Exit($shared.SyncRoot) }
    }

    function Invoke-Chat($ctx, $S, $session, $message, $mode) {
        $shared = $S.shared
        [System.Threading.Monitor]::Enter($shared.SyncRoot)
        try { $resume = $shared.seen.ContainsKey($session); $shared.seen[$session] = $true }
        finally { [System.Threading.Monitor]::Exit($shared.SyncRoot) }

        $resp = $ctx.Response
        $resp.StatusCode = 200
        $resp.ContentType = 'application/x-ndjson; charset=utf-8'
        $resp.SendChunked = $true
        try { $resp.Headers['Cache-Control'] = 'no-cache' } catch {}
        $out = $resp.OutputStream

        $lockObj = Get-SessionLock $shared $session
        if (-not [System.Threading.Monitor]::TryEnter($lockObj, [int]($S.timeoutSec * 1000))) {
            [void](Emit $out @{ type = 'error'; text = 'session busy' })
            [void](Emit $out @{ type = 'done'; code = -1 })
            return
        }

        $proc = $null
        try {
            [void](Emit $out @{ type = 'meta'; session = $session; resume = $resume; mode = $mode })

            $psi = New-Object System.Diagnostics.ProcessStartInfo
            $psi.FileName = $S.gooseBin
            $gargs = @('run', '-n', $session, '--max-turns', [string]$S.maxTurns)
            if ($resume) { $gargs += '-r' }
            $gargs += @('-i', '-')          # message arrives on STDIN, never the command line
            $psi.Arguments = ($gargs -join ' ')   # session is sanitized [A-Za-z0-9_.-]; no quoting needed
            $psi.UseShellExecute = $false
            $psi.RedirectStandardInput = $true
            $psi.RedirectStandardOutput = $true
            $psi.RedirectStandardError = $true
            $psi.StandardOutputEncoding = $UTF8
            $psi.StandardErrorEncoding = $UTF8
            $psi.WorkingDirectory = $S.workspace
            $psi.EnvironmentVariables['GOOSE_MODE'] = $mode   # per-process: no cross-runspace race

            $proc = [System.Diagnostics.Process]::Start($psi)
            $errTask = $proc.StandardError.ReadToEndAsync()   # drain stderr async (avoid deadlock)
            $si = New-Object System.IO.StreamWriter($proc.StandardInput.BaseStream, $UTF8)
            $si.Write($message); $si.Flush(); $si.Close()

            $so = $proc.StandardOutput
            $deadline = (Get-Date).AddSeconds($S.timeoutSec)
            $reAnsi = $S.reAnsi; $reMascot = $S.reMascot; $reRule = $S.reRule; $reTool = $S.reTool
            $inTool = $false; $toolBuf = New-Object System.Collections.Generic.List[string]
            $alive = $true; $killed = $false

            while ($true) {
                $task = $so.ReadLineAsync()
                while (-not $task.Wait(1000)) {
                    if ((Get-Date) -gt $deadline) { try { if (-not $proc.HasExited) { $proc.Kill() } } catch {}; $killed = $true; break }
                }
                if ($killed) { break }
                $raw = $task.Result
                if ($null -eq $raw) { break }                 # EOF

                $line = $reAnsi.Replace([string]$raw, '').Replace("`r", '').TrimEnd("`n")
                if ($reMascot.IsMatch($line)) { continue }

                $mt = $reTool.Match($line)
                if ($mt.Success) {
                    if ($inTool) { if ($toolBuf.Count -gt 0) { [void](Emit $out @{ type = 'tool_args'; text = ($toolBuf -join "`n") }) }; $inTool = $false; $toolBuf.Clear() }
                    $parts = $mt.Groups[1].Value -split '\s+'
                    $nm = $parts[0]; $ex = if ($parts.Count -gt 1) { $parts[1] } else { '' }
                    $inTool = $true; $toolBuf.Clear()
                    $alive = Emit $out @{ type = 'tool_start'; name = $nm; ext = $ex }
                    if (-not $alive) { break }
                    continue
                }
                if ($inTool) {
                    if ($line.Trim() -eq '') { if ($toolBuf.Count -gt 0) { [void](Emit $out @{ type = 'tool_args'; text = ($toolBuf -join "`n") }) }; $inTool = $false; $toolBuf.Clear(); continue }
                    if ($raw.Length -gt 0 -and ($raw[0] -eq ' ' -or $raw[0] -eq [char]9)) { $toolBuf.Add($line.Trim()); continue }
                    if ($toolBuf.Count -gt 0) { [void](Emit $out @{ type = 'tool_args'; text = ($toolBuf -join "`n") }) }; $inTool = $false; $toolBuf.Clear()
                }
                if ($reRule.IsMatch($line)) { continue }
                $alive = Emit $out @{ type = 'text'; text = ($line + "`n") }
                if (-not $alive) { break }
            }
            if ($inTool) { if ($toolBuf.Count -gt 0) { [void](Emit $out @{ type = 'tool_args'; text = ($toolBuf -join "`n") }) }; $inTool = $false; $toolBuf.Clear() }

            if ($killed) {
                [void](Emit $out @{ type = 'error'; text = "timed out after $($S.timeoutSec)s" })
                [void](Emit $out @{ type = 'done'; code = -1 })
            } elseif ($alive) {
                try { $proc.WaitForExit() } catch {}
                $code = try { $proc.ExitCode } catch { -1 }
                if ($code -ne 0) {
                    $errText = ''; try { $errText = $errTask.Result } catch {}
                    if ($errText -and $errText.Trim()) { [void](Emit $out @{ type = 'error'; text = $errText.Trim() }) }
                }
                [void](Emit $out @{ type = 'done'; code = $code })
            }
        } catch {
            [void](Emit $out @{ type = 'error'; text = ([string]$_) })
            [void](Emit $out @{ type = 'done'; code = -1 })
        } finally {
            if ($proc -and -not $proc.HasExited) { try { $proc.Kill() } catch {} }
            [System.Threading.Monitor]::Exit($lockObj)
            try { $out.Close() } catch {}
        }
    }

    function Handle-Chat($ctx, $S) {
        if ($S.token) {
            $supplied = $ctx.Request.Headers['X-Goose-Token']
            if (-not $supplied) { $supplied = $ctx.Request.QueryString['token'] }
            if ($supplied -ne $S.token) { Send-Json $ctx @{ error = 'unauthorized' } 401; return }
        }
        $encR = if ($ctx.Request.ContentEncoding) { $ctx.Request.ContentEncoding } else { [System.Text.Encoding]::UTF8 }
        $reader = New-Object System.IO.StreamReader($ctx.Request.InputStream, $encR)
        $bodyText = $reader.ReadToEnd(); $reader.Close()
        $req = $null
        try { if ($bodyText.Trim()) { $req = $bodyText | ConvertFrom-Json } } catch { Send-Json $ctx @{ error = 'bad json' } 400; return }
        if ($null -eq $req) { Send-Json $ctx @{ error = 'bad json' } 400; return }

        $session = if ($req.session) { [string]$req.session } else { 'web' }
        $session = $session.Trim(); if ($session.Length -gt 80) { $session = $session.Substring(0, 80) }
        $session = [regex]::Replace($session, '[^A-Za-z0-9_.-]', '_'); if (-not $session) { $session = 'web' }
        $message = if ($req.message) { ([string]$req.message).Trim() } else { '' }
        $mode = if ($req.mode -eq 'chat') { 'chat' } else { 'auto' }
        if (-not $message) { Send-Json $ctx @{ error = 'empty message' } 400; return }

        Invoke-Chat $ctx $S $session $message $mode
    }

    function Handle-Request($ctx, $S) {
        $streamed = $false
        try {
            $req = $ctx.Request
            $path = $req.Url.AbsolutePath
            if ($req.HttpMethod -eq 'GET') {
                if ($path -eq '/' -or $path -eq '/index.html') { Send-File $ctx $S.indexPath 'text/html; charset=utf-8' }
                elseif ($path -eq '/api/health') { Send-Json $ctx (Build-Health $S) }
                else { Send-Json $ctx @{ error = 'not found' } 404 }
            } elseif ($req.HttpMethod -eq 'POST' -and $path -eq '/api/chat') {
                $streamed = $true; Handle-Chat $ctx $S
            } else {
                Send-Json $ctx @{ error = 'not found' } 404
            }
        } catch {
            if (-not $streamed) { try { Send-Json $ctx @{ error = ([string]$_) } 500 } catch {} }
        } finally {
            try { $ctx.Response.Close() } catch {}
        }
    }

    # accept loop -- GetContext is thread-safe across the worker runspaces
    while ($listener.IsListening) {
        $ctx = $null
        try { $ctx = $listener.GetContext() } catch { break }   # listener stopped
        if ($ctx) { Handle-Request $ctx $S }
    }
}

# ----------------------------------------------------------------------------
# start N worker runspaces, then idle until Ctrl+C
# ----------------------------------------------------------------------------
$N = 6
$pool = [runspacefactory]::CreateRunspacePool(1, $N)
$pool.Open()
$jobs = @()
for ($i = 0; $i -lt $N; $i++) {
    $ps = [powershell]::Create()
    $ps.RunspacePool = $pool
    [void]$ps.AddScript($worker.ToString()).AddArgument($listener).AddArgument($S)
    $jobs += @{ ps = $ps; handle = $ps.BeginInvoke() }
}

try {
    while ($listener.IsListening) { Start-Sleep -Seconds 2 }
} finally {
    Write-Host "`nshutting down."
    try { $listener.Stop() } catch {}
    try { $listener.Close() } catch {}
    foreach ($j in $jobs) { try { $j.ps.Stop() } catch {}; try { $j.ps.Dispose() } catch {} }
    try { $pool.Close() } catch {}
}
