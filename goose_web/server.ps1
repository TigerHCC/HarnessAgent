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
$UploadsSubdir  = if ($CFG.uploads_subdir) { ([string]$CFG.uploads_subdir).Trim('/\') } else { 'uploads' }
$MaxUploadMb    = if ($env:GOOSE_WEB_MAX_UPLOAD_MB) { [int]$env:GOOSE_WEB_MAX_UPLOAD_MB } elseif ($CFG.max_upload_mb) { [int]$CFG.max_upload_mb } else { 25 }
$MaxUploadBytes = $MaxUploadMb * 1024 * 1024

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
# Live MCP tool discovery
# ----------------------------------------------------------------------------
# The sidebar tool list is discovered LIVE from goose's own config.yaml instead
# of being hardcoded. We parse the `extensions:` block (a tiny YAML subset), then
# handshake each enabled extension for its real tool set:
#   builtin (developer) -> curated static list (developer is in-process and NOT
#                          handshakeable: `goose mcp developer` is invalid)
#   stdio               -> spawn cmd+args, newline-delimited JSON-RPC handshake
#   streamable_http     -> HttpWebRequest POST initialize / initialized / tools/list
# A daemon runspace fills $Shared.exts/$Shared.tools now and refreshes every
# $DISCOVERY_REFRESH_SEC; Build-Health only reads the cached snapshot.
$DISCOVERY_REFRESH_SEC = 90

function Resolve-GooseConfig {
    if ($env:GOOSE_CONFIG) { return $env:GOOSE_CONFIG }
    if ($env:APPDATA) { return (Join-Path $env:APPDATA 'Block\goose\config\config.yaml') }
    if ($HOME)        { return (Join-Path $HOME '.config/goose/config.yaml') }
    return 'config.yaml'
}
$GooseConfigPath = Resolve-GooseConfig

# All discovery helpers live in this string so they can be injected verbatim into
# the discoverer runspace (runspaces do not inherit the parent's functions). We
# also dot-source it into the main scope below to seed the snapshot synchronously.
$DiscoveryFns = @'
function Get-DeveloperTools {
    return @(
        @{ name = 'shell';       description = 'Run a shell command' },
        @{ name = 'text_editor'; description = 'View, write, and edit files' }
    )
}

function Short-Desc($s) {
    if (-not $s) { return '' }
    $t = ([string]$s).Trim() -replace '\s+', ' '
    $dot = $t.IndexOf('. ')
    if ($dot -gt 0 -and $dot -lt 80) { return $t.Substring(0, $dot + 1) }
    if ($t.Length -gt 80) { return $t.Substring(0, 77) + '...' }
    return $t
}

# Minimal YAML-subset parser for goose's `extensions:` block only.
function Parse-GooseExtensions($path) {
    $exts = @()
    if (-not (Test-Path -LiteralPath $path)) { return $exts }
    $lines = Get-Content -LiteralPath $path -Encoding UTF8
    $inExt = $false; $cur = $null; $inArgs = $false
    foreach ($raw in $lines) {
        $line = ([string]$raw).Replace("`t", '    ')
        if ($line.Trim() -eq '' -or $line.TrimStart().StartsWith('#')) { continue }
        $indent = $line.Length - $line.TrimStart(' ').Length
        if ($indent -eq 0) {
            if ($line -match '^extensions:\s*$') { $inExt = $true }
            elseif ($inExt) { break }   # left the extensions block
            continue
        }
        if (-not $inExt) { continue }
        $content = $line.Trim()
        if ($indent -le 2 -and $content -match '^([A-Za-z0-9_.\-]+):\s*$') {
            if ($cur) { $exts += $cur }
            $cur = @{ id = $Matches[1]; type = ''; enabled = $true; name = $Matches[1]; cmd = ''; args = @(); uri = '' }
            $inArgs = $false
            continue
        }
        if (-not $cur) { continue }
        if ($inArgs) {
            if ($content -match '^-\s*(.+)$') { $cur.args += $Matches[1].Trim(); continue }
            $inArgs = $false
        }
        if ($content -match '^args:\s*$') { $inArgs = $true; $cur.args = @(); continue }
        if ($content -match '^([A-Za-z0-9_]+):\s*(.*)$') {
            $k = $Matches[1]; $v = $Matches[2].Trim()
            switch ($k) {
                'type'    { $cur.type = $v }
                'enabled' { $cur.enabled = ($v -eq 'true') }
                'name'    { if ($v) { $cur.name = $v } }
                'cmd'     { $cur.cmd = $v }
                'uri'     { $cur.uri = $v }
            }
        }
    }
    if ($cur) { $exts += $cur }
    return $exts
}

function ConvertFrom-McpBody($text) {
    if (-not $text) { return $null }
    $t = [string]$text
    if ($t.TrimStart().StartsWith('{')) { $jsonStr = $t }
    else {
        $m = [regex]::Match($t, 'data:\s*(\{.*\})')
        if ($m.Success) { $jsonStr = $m.Groups[1].Value } else { return $null }
    }
    try { return ($jsonStr | ConvertFrom-Json) } catch { return $null }
}

function Invoke-McpHttp($uri, $bodyObj, $sessionId, $timeoutMs = 10000) {
    $json  = $bodyObj | ConvertTo-Json -Depth 8 -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    $req = [System.Net.HttpWebRequest]::Create($uri)
    $req.Method = 'POST'; $req.ContentType = 'application/json'; $req.Accept = 'application/json, text/event-stream'
    $req.Timeout = $timeoutMs; $req.ReadWriteTimeout = $timeoutMs; $req.Proxy = $null
    if ($sessionId) { $req.Headers['Mcp-Session-Id'] = $sessionId }
    $req.ContentLength = $bytes.Length
    $rs = $req.GetRequestStream(); $rs.Write($bytes, 0, $bytes.Length); $rs.Close()
    $resp = $req.GetResponse()
    $sid = $resp.Headers['Mcp-Session-Id']
    $sr = New-Object System.IO.StreamReader($resp.GetResponseStream())
    $body = $sr.ReadToEnd(); $sr.Close(); $resp.Close()
    return @{ sid = $sid; text = $body }
}

# streamable_http MCP handshake: initialize -> initialized -> tools/list.
function Get-McpHttpTools($uri) {
    $init = @{ jsonrpc = '2.0'; id = 1; method = 'initialize'; params = @{ protocolVersion = '2025-06-18'; capabilities = @{}; clientInfo = @{ name = 'goose_web'; version = '1' } } }
    $r1  = Invoke-McpHttp $uri $init $null
    $sid = $r1.sid
    try { [void](Invoke-McpHttp $uri @{ jsonrpc = '2.0'; method = 'notifications/initialized' } $sid) } catch {}
    $r2  = Invoke-McpHttp $uri @{ jsonrpc = '2.0'; id = 2; method = 'tools/list' } $sid
    $obj = ConvertFrom-McpBody $r2.text
    $tools = @()
    if ($obj -and $obj.result -and $obj.result.tools) {
        foreach ($t in $obj.result.tools) { $tools += @{ name = [string]$t.name; description = [string]$t.description } }
    }
    return $tools
}

# stdio MCP handshake over a spawned process (newline-delimited JSON-RPC).
function Get-McpStdioTools($exe, $argList) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $exe; $psi.Arguments = (@($argList) -join ' ')
    $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
    $psi.RedirectStandardInput = $true; $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    $errTask = $p.StandardError.ReadToEndAsync()   # drain stderr async (avoid deadlock)
    $si = $p.StandardInput
    $si.WriteLine('{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"goose_web","version":"1"}}}')
    $si.WriteLine('{"jsonrpc":"2.0","method":"notifications/initialized"}')
    $si.WriteLine('{"jsonrpc":"2.0","id":2,"method":"tools/list"}')
    $si.Flush()
    $deadline = (Get-Date).AddSeconds(25)
    $tools = @()
    while ((Get-Date) -lt $deadline) {
        $task = $p.StandardOutput.ReadLineAsync()
        if (-not $task.Wait(1000)) { continue }
        $line = $task.Result
        if ($null -eq $line) { break }
        if ($line -notmatch '^\s*\{') { continue }
        try { $obj = $line | ConvertFrom-Json } catch { continue }
        if ($obj.id -eq 2 -and $obj.result -and $obj.result.tools) {
            foreach ($t in $obj.result.tools) { $tools += @{ name = [string]$t.name; description = [string]$t.description } }
            break
        }
    }
    try { $si.Close() } catch {}
    try { if (-not $p.HasExited) { $p.Kill() } } catch {}
    return $tools
}

# Discover one extension -> @{ ext = <health entry>; tools = <flat rows> }.
function Discover-Extension($e, $gooseBin) {
    $detail = ''; $status = 'offline'; $etools = @()
    if ($e.type -eq 'builtin') {
        if ($e.id -eq 'developer') {            # not handshakeable -> static
            $status = 'builtin'; $etools = @(Get-DeveloperTools)
        } else {                                 # introspect via `goose mcp <id>`
            try { $etools = @(Get-McpStdioTools $gooseBin @('mcp', $e.id)); $status = 'builtin' } catch { $status = 'offline'; $etools = @() }
        }
    } elseif ($e.type -eq 'stdio') {
        try { $etools = @(Get-McpStdioTools $e.cmd $e.args); $status = 'ok' } catch { $status = 'offline'; $etools = @() }
    } elseif ($e.type -eq 'streamable_http' -or $e.type -eq 'sse') {
        if ($e.uri) { try { $u = [System.Uri]$e.uri; $detail = "$($u.Host):$($u.Port)" } catch {} }
        try { $etools = @(Get-McpHttpTools $e.uri); $status = 'ok' } catch { $status = 'offline'; $etools = @() }
    } else {
        $status = 'unknown'
    }
    $rows = @()
    foreach ($t in $etools) { $rows += @{ group = $e.id; name = $t.name; desc = (Short-Desc $t.description) } }
    return @{ ext = @{ id = $e.id; name = $e.name; transport = $e.type; status = $status; count = @($etools).Count; detail = $detail }; tools = $rows }
}
'@

# Seed the snapshot synchronously (cheap config parse) so /api/health is never
# empty; the daemon below replaces it with real tool counts within seconds.
. ([scriptblock]::Create($DiscoveryFns))
$seedExts = @()
try {
    foreach ($e in (Parse-GooseExtensions $GooseConfigPath)) {
        if (-not $e.enabled) { continue }
        $detail = ''
        if ($e.uri) { try { $u = [System.Uri]$e.uri; $detail = "$($u.Host):$($u.Port)" } catch {} }
        $status = if ($e.type -eq 'builtin' -and $e.id -eq 'developer') { 'builtin' } else { 'checking' }
        $seedExts += @{ id = $e.id; name = $e.name; transport = $e.type; status = $status; count = 0; detail = $detail }
    }
} catch {}
[System.Threading.Monitor]::Enter($Shared.SyncRoot)
try { $Shared.exts = $seedExts; $Shared.tools = @() } finally { [System.Threading.Monitor]::Exit($Shared.SyncRoot) }

# The discoverer runspace: handshake every enabled extension, publish the snapshot
# under the shared lock, then refresh on an interval. Never touched by /api/health.
$discoverer = {
    param($configPath, $refreshSec, $shared, $fnsText, $gooseBin)
    Invoke-Expression $fnsText
    while ($true) {
        $exts = @(); $tools = @()
        try {
            foreach ($e in (Parse-GooseExtensions $configPath)) {
                if (-not $e.enabled) { continue }
                $d = Discover-Extension $e $gooseBin
                $exts += $d.ext
                foreach ($r in $d.tools) { $tools += $r }
            }
        } catch {}
        [System.Threading.Monitor]::Enter($shared.SyncRoot)
        try { $shared.exts = $exts; $shared.tools = $tools } finally { [System.Threading.Monitor]::Exit($shared.SyncRoot) }
        Start-Sleep -Seconds $refreshSec
    }
}

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
Write-Host "  tools     : live discovery every ${DISCOVERY_REFRESH_SEC}s <- $GooseConfigPath"
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
    uploadsSubdir = $UploadsSubdir; maxUploadBytes = $MaxUploadBytes
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

    # ---- uploads: filename safety + attachment message composition (parity with server.py) ----
    function Safe-Session($s) {
        $s = ([string]$s).Trim()
        $s = [regex]::Replace($s, '[^A-Za-z0-9_.-]', '_')
        if ($s.Length -gt 80) { $s = $s.Substring(0, 80) }
        $s = $s.Trim('.')
        if (-not $s) { 'web' } else { $s }
    }
    function Safe-Name($name) {
        $n = ([string]$name) -replace '\\', '/'
        $n = ($n -split '/')[-1]
        $n = ($n.Trim() -replace '[^A-Za-z0-9._ ()-]', '_').TrimStart('.').Trim()
        if ($n.Length -gt 150) { $n = $n.Substring(0, 150) }
        $n = $n.Trim()
        if (-not $n) { 'file' } else { $n }
    }
    function Human-Size($n) {
        $f = [double]$n
        foreach ($u in 'B', 'KB', 'MB', 'GB') {
            if ($f -lt 1024 -or $u -eq 'GB') {
                if ($u -eq 'B') { return ("{0} B" -f [int]$f) } else { return ("{0:0.0} {1}" -f $f, $u) }
            }
            $f = $f / 1024
        }
    }
    function Session-UploadDir($S, $session) {
        $root = [System.IO.Path]::GetFullPath((Join-Path $S.workspace $S.uploadsSubdir))
        $d = [System.IO.Path]::GetFullPath((Join-Path $root (Safe-Session $session)))
        $sep = [System.IO.Path]::DirectorySeparatorChar
        if ($d -ne $root -and -not $d.StartsWith($root + $sep)) { throw 'escapes workspace' }
        return $d
    }
    function Unique-Path($dir, $name) {
        $p = Join-Path $dir $name
        if (-not (Test-Path -LiteralPath $p)) { return $p }
        $base = [System.IO.Path]::GetFileNameWithoutExtension($name)
        $ext = [System.IO.Path]::GetExtension($name)
        $i = 1
        while (Test-Path -LiteralPath (Join-Path $dir ("$base ($i)$ext"))) { $i++ }
        return (Join-Path $dir ("$base ($i)$ext"))
    }
    function Compose-Message($S, $message, $session, $attachments) {
        if (-not $attachments) { return $message }
        $dir = Session-UploadDir $S $session
        $sub = "$($S.uploadsSubdir)/$(Safe-Session $session)"
        $lines = @()
        foreach ($a in $attachments) {
            if ($a -isnot [string]) { continue }
            $sn = Safe-Name $a
            $p = Join-Path $dir $sn
            if (Test-Path -LiteralPath $p -PathType Leaf) {
                $lines += "- $sub/$sn ($(Human-Size (Get-Item -LiteralPath $p).Length))"
            }
        }
        if ($lines.Count -eq 0) { return $message }
        # Chinese built from code points (file has no BOM; keep it ASCII-on-disk like the regex markers)
        $label = -join (@(0x5B,0x9644,0x52A0,0x6A94,0x6848,0x20,0x28,0x76F8,0x5C0D,0x65BC,0x5DE5,0x4F5C,0x76EE,0x9304,0x29,0x3A,0x5D) | ForEach-Object { [char]$_ })
        $deflt = -join (@(0x8ACB,0x67E5,0x770B,0x6211,0x9644,0x52A0,0x7684,0x6A94,0x6848,0x3002) | ForEach-Object { [char]$_ })
        $body = if ($message) { $message } else { $deflt }
        return ($body + "`n`n" + $label + "`n" + ($lines -join "`n"))
    }
    function Handle-Upload($ctx, $S) {
        if ($S.token) {
            $sup = $ctx.Request.Headers['X-Goose-Token']
            if (-not $sup) { $sup = $ctx.Request.QueryString['token'] }
            if ($sup -ne $S.token) { Send-Json $ctx @{ error = 'unauthorized' } 401; return }
        }
        $session = $ctx.Request.QueryString['session']; if (-not $session) { $session = 'web' }
        $name = Safe-Name $ctx.Request.QueryString['name']
        $len = [int64]$ctx.Request.ContentLength64
        if ($len -le 0) { Send-Json $ctx @{ error = 'empty body' } 400; return }
        if ($len -gt $S.maxUploadBytes) { Send-Json $ctx @{ error = 'file too large' } 413; return }
        $dest = $null
        try {
            $dir = Session-UploadDir $S $session
            New-Item -ItemType Directory -Force -Path $dir | Out-Null
            $dest = Unique-Path $dir $name
            $fs = [System.IO.File]::Create($dest)
            try { $ctx.Request.InputStream.CopyTo($fs) } finally { $fs.Close() }
            $size = (Get-Item -LiteralPath $dest).Length
            Send-Json $ctx @{ ok = $true; name = [System.IO.Path]::GetFileName($dest); size = $size }
        } catch {
            if ($dest -and (Test-Path -LiteralPath $dest)) { try { Remove-Item -LiteralPath $dest -Force } catch {} }
            Send-Json $ctx @{ error = ([string]$_) } 400
        }
    }

    function Build-Health($S) {
        $cfg = $S.cfg
        $backends = @()
        foreach ($b in $cfg.backends) {
            $u = ([string]$b.url).TrimEnd('/')
            $hp = if ($b.health_path) { [string]$b.health_path } else { '/' }
            $backends += @{ name = [string]$b.name; detail = $u; ok = (Test-Url ($u + $hp)) }
        }
        # tool list is live-discovered in a background runspace; read the cached
        # snapshot only (never block here). $shared.exts/.tools are seeded at startup.
        $shared = $S.shared
        $exts = @(); $tools = @()
        [System.Threading.Monitor]::Enter($shared.SyncRoot)
        try {
            if ($shared.exts)  { $exts  = @($shared.exts) }
            if ($shared.tools) { $tools = @($shared.tools) }
        } finally { [System.Threading.Monitor]::Exit($shared.SyncRoot) }
        return @{
            ok = $true; version = $S.gooseVer; model = [string]$cfg.model
            provider = ([string]$cfg.provider_label + ' @ ' + (Get-ChatUrl $cfg))
            workspace = [string]$S.workspace; token_required = [bool]$S.token
            backends = $backends; extensions = $exts; tools = $tools
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
            $psi.EnvironmentVariables['GOOSE_TELEMETRY_ENABLED'] = 'false'   # privacy: never upload usage telemetry

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

        $session = Safe-Session $req.session
        $message = if ($req.message) { ([string]$req.message).Trim() } else { '' }
        $mode = if ($req.mode -eq 'chat') { 'chat' } else { 'auto' }
        $message = Compose-Message $S $message $session $req.attachments
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
            } elseif ($req.HttpMethod -eq 'POST' -and $path -eq '/api/upload') {
                Handle-Upload $ctx $S
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

# background MCP discovery -- its own runspace (the worker pool is saturated by the
# blocking accept loops). Does the real handshakes now, then refreshes every 90s.
$discoPs = [powershell]::Create()
[void]$discoPs.AddScript($discoverer.ToString()).AddArgument($GooseConfigPath).AddArgument($DISCOVERY_REFRESH_SEC).AddArgument($Shared).AddArgument($DiscoveryFns).AddArgument($GooseBin)
$discoHandle = $discoPs.BeginInvoke()

try {
    while ($listener.IsListening) { Start-Sleep -Seconds 2 }
} finally {
    Write-Host "`nshutting down."
    try { $listener.Stop() } catch {}
    try { $listener.Close() } catch {}
    try { $discoPs.Stop() } catch {}; try { $discoPs.Dispose() } catch {}
    foreach ($j in $jobs) { try { $j.ps.Stop() } catch {}; try { $j.ps.Dispose() } catch {} }
    try { $pool.Close() } catch {}
}
