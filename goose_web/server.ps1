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
  GOOSE_WEB_* environment variable overrides the matching value. The model and
  provider shown by /api/health are NOT taken from config.json: they are read
  live from goose's own config.yaml (GOOSE_PROVIDER / GOOSE_MODEL /
  OPENAI_HOST / OLLAMA_HOST, env vars winning) on every poll, so the UI always
  shows what goose actually uses. See README.

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
        max_upload_mb   = 25
        uploads_subdir  = 'uploads'
        goose_bin       = ''
        model           = 'qwen-3.6-chat'   # last-resort fallback; live value comes from goose's config.yaml
        provider_label  = ''                # legacy single-label schema (maps to provider_labels['openai'])
        backends        = @(
            @{ name = 'vLLM chat';  url = 'http://100.88.242.174:8000';  health_path = '/v1/models'; role = 'chat'  }
            @{ name = 'vLLM embed'; url = 'http://100.88.242.174:8001';  health_path = '/v1/models'; role = 'embed' }
            @{ name = 'Ollama';     url = 'http://100.88.242.174:11434'; health_path = '/api/tags';  role = 'ollama' }
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
$Shared = [hashtable]::Synchronized(@{ seen = @{}; locks = @{}; cfgWriteLock = (New-Object object)
                                       refreshSignal = (New-Object System.Threading.ManualResetEvent($false)) })

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

# --- live provider truth: what goose ACTUALLY runs (model / provider / hosts) ---
# goose's real provider+model+endpoints are top-level scalars in its own
# config.yaml (GOOSE_PROVIDER / GOOSE_MODEL / OPENAI_HOST / OLLAMA_HOST), with
# process env vars overriding the file -- goose's own precedence. Build-Health
# re-reads them on every /api/health poll (the file is tiny) so the UI always
# shows what goose actually uses; config.json only supplies panel layout +
# fallbacks.
function Read-GooseScalars($path) {
    $out = @{}
    try {
        if (Test-Path -LiteralPath $path) {
            foreach ($raw in (Get-Content -LiteralPath $path -Encoding UTF8)) {
                $m = [regex]::Match([string]$raw, '^([A-Za-z0-9_]+):\s*(.*)$')   # column-0 scalars only (skips comments/indented)
                if (-not $m.Success) { continue }
                if ($m.Groups[1].Value -in 'GOOSE_PROVIDER','GOOSE_MODEL','OPENAI_HOST','OPENAI_BASE_PATH','OLLAMA_HOST') {
                    $v = ([string]$m.Groups[2].Value -split '\s#')[0]            # strip inline comment
                    $out[$m.Groups[1].Value] = $v.Trim().Trim('"').Trim("'")
                }
            }
        }
    } catch {}
    return $out
}

function Normalize-HostUrl($url, $defaultPort) {
    $u = ([string]$url).Trim().TrimEnd('/')
    if (-not $u) { return '' }
    if ($u -notmatch '://') { $u = 'http://' + $u }
    if ($defaultPort -and $u -notmatch '^[a-z+.\-]+://[^/]*:\d+') { $u = $u + ':' + $defaultPort }
    return $u
}

function Get-ProviderLabels($cfg) {
    $labels = @{ openai = 'vLLM (OpenAI-compat)'; ollama = 'Ollama' }
    if ($cfg.provider_label) { $labels['openai'] = [string]$cfg.provider_label }   # legacy single-label schema
    $pl = $cfg.provider_labels
    if ($pl -is [hashtable]) { foreach ($k in @($pl.Keys)) { $labels[([string]$k).ToLower()] = [string]$pl[$k] } }
    elseif ($pl) { foreach ($p in $pl.PSObject.Properties) { $labels[([string]$p.Name).ToLower()] = [string]$p.Value } }
    return $labels
}

# What goose actually runs right now: env > goose config.yaml > config.json.
# Returned keys: provider, model, label, host (live endpoint of the active
# provider, '' if unknown), hosts (role -> live URL), active_role.
function Get-ProviderSnapshot($cfg, $configPath) {
    $y = Read-GooseScalars $configPath
    $provider = ''
    if ($y['GOOSE_PROVIDER'])   { $provider = $y['GOOSE_PROVIDER'] }
    if ($env:GOOSE_PROVIDER)    { $provider = $env:GOOSE_PROVIDER }
    $provider = ([string]$provider).Trim().ToLower()
    $model = [string]$cfg.model
    if ($y['GOOSE_MODEL'])      { $model = $y['GOOSE_MODEL'] }
    if ($env:GOOSE_MODEL)       { $model = $env:GOOSE_MODEL }
    if ($env:GOOSE_WEB_MODEL)   { $model = $env:GOOSE_WEB_MODEL }
    $chatRaw = $y['OPENAI_HOST'];   if ($env:OPENAI_HOST) { $chatRaw = $env:OPENAI_HOST }
    $ollamaRaw = $y['OLLAMA_HOST']; if ($env:OLLAMA_HOST) { $ollamaRaw = $env:OLLAMA_HOST }
    $hosts = @{ chat = (Normalize-HostUrl $chatRaw $null); ollama = (Normalize-HostUrl $ollamaRaw 11434) }
    $role = ''
    if ($provider -eq 'openai') { $role = 'chat' } elseif ($provider -eq 'ollama') { $role = 'ollama' }
    $provHost = ''
    if ($role) { $provHost = [string]$hosts[$role] }
    $labels = Get-ProviderLabels $cfg
    $label = [string]$labels['openai']
    if ($provider) { $label = $provider }
    if ($labels[$provider]) { $label = [string]$labels[$provider] }
    return @{ provider = $provider; model = ([string]$model).Trim(); label = $label
              host = $provHost; hosts = $hosts; active_role = $role }
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
    $togglable = Test-Togglable $e
    if (-not $e.enabled) {
        if ($e.uri) { try { $u = [System.Uri]$e.uri; $detail = "$($u.Host):$($u.Port)" } catch {} }
        return @{ ext = @{ id = $e.id; name = $e.name; transport = $e.type; status = 'disabled'; count = 0; detail = $detail; enabled = $false; togglable = $togglable }; tools = @() }
    }
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
    return @{ ext = @{ id = $e.id; name = $e.name; transport = $e.type; status = $status; count = @($etools).Count; detail = $detail; enabled = $true; togglable = $togglable }; tools = $rows }
}

# --- scheduler control (Task 8): pure helpers + MCP tools/call wrapper --------
# Kept byte-identical in goose_web/schedules_helpers_under_test.ps1 so the unit
# test can dot-source them without pulling in the whole $DiscoveryFns string.
function Merge-ConfirmArgs($arguments, $token) {
    $out = @{}; foreach ($k in $arguments.Keys) { $out[$k] = $arguments[$k] }
    $out['confirm_token'] = $token
    return $out
}
function Parse-McpResult($obj) {
    if ($null -eq $obj -or $null -eq $obj.result) { return $null }
    if ($obj.result.PSObject.Properties['structuredContent'] -and $obj.result.structuredContent) {
        return $obj.result.structuredContent
    }
    if ($obj.result.content) {
        foreach ($c in $obj.result.content) {
            if ($c.type -eq 'text' -and $c.text) { try { return ($c.text | ConvertFrom-Json) } catch {} }
        }
    }
    return $obj.result
}

function Resolve-SchedulerUri($configPath) {
    foreach ($e in (Parse-GooseExtensions $configPath)) {
        if ($e.id -eq 'scheduler' -and $e.uri) { return $e.uri }
    }
    return 'http://127.0.0.1:8793/mcp'          # fallback: canonical loopback endpoint
}

function Invoke-SchedulerTool($uri, $name, $arguments, $timeoutMs = 8000) {
    # initialize -> tools/call, then auto-confirm the preview->confirm two-step (the UI click IS the
    # human confirmation, so goose_web completes it without weakening the agent-path gate).
    $init = @{ jsonrpc='2.0'; id=1; method='initialize'; params=@{ protocolVersion='2025-06-18'; capabilities=@{}; clientInfo=@{ name='goose_web'; version='1' } } }
    $r1 = Invoke-McpHttp $uri $init $null $timeoutMs
    $sid = $r1.sid
    try { [void](Invoke-McpHttp $uri @{ jsonrpc='2.0'; method='notifications/initialized' } $sid $timeoutMs) } catch {}
    $callBody = @{ jsonrpc='2.0'; id=2; method='tools/call'; params=@{ name=$name; arguments=$arguments } }
    $r2 = Invoke-McpHttp $uri $callBody $sid $timeoutMs
    $res = Parse-McpResult (ConvertFrom-McpBody $r2.text)
    if ($res -and $res.PSObject.Properties['requires_confirmation'] -and $res.requires_confirmation -and $res.confirm_token) {
        $confArgs = Merge-ConfirmArgs $arguments $res.confirm_token
        $callBody2 = @{ jsonrpc='2.0'; id=3; method='tools/call'; params=@{ name=$name; arguments=$confArgs } }
        $r3 = Invoke-McpHttp $uri $callBody2 $sid $timeoutMs
        $res = Parse-McpResult (ConvertFrom-McpBody $r3.text)
    }
    return $res
}
'@

# Fold in the per-MCP toggle helpers and the UTF-8 request decoders so both the main
# scope (seed) and the worker/discoverer runspaces (which Invoke-Expression this text) get them.
$ToggleFns = ''
try { $ToggleFns = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $Here 'mcp_toggle.ps1') } catch { Write-Warning "[goose_web] could not load mcp_toggle.ps1: $_" }
$EncodingFns = ''
try { $EncodingFns = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $Here 'http_encoding.ps1') } catch { Write-Warning "[goose_web] could not load http_encoding.ps1: $_" }
$ProfilesFns = ''
try { $ProfilesFns = Get-Content -Raw -Encoding UTF8 -LiteralPath (Join-Path $Here 'profiles_helpers.ps1') } catch { Write-Warning "[goose_web] could not load profiles_helpers.ps1: $_" }
$DiscoveryFns = $DiscoveryFns + "`n" + $ToggleFns + "`n" + $EncodingFns + "`n" + $ProfilesFns

# Seed the snapshot synchronously (cheap config parse) so /api/health is never
# empty; the daemon below replaces it with real tool counts within seconds.
. ([scriptblock]::Create($DiscoveryFns))
$seedExts = @()
try {
    foreach ($e in (Parse-GooseExtensions $GooseConfigPath)) {
        $togglable = Test-Togglable $e
        if (-not $e.enabled -and -not $togglable) { continue }
        $detail = ''
        if ($e.uri) { try { $u = [System.Uri]$e.uri; $detail = "$($u.Host):$($u.Port)" } catch {} }
        $status = if (-not $e.enabled) { 'disabled' } elseif ($e.type -eq 'builtin' -and $e.id -eq 'developer') { 'builtin' } else { 'checking' }
        $seedExts += @{ id = $e.id; name = $e.name; transport = $e.type; status = $status; count = 0; detail = $detail; enabled = [bool]$e.enabled; togglable = $togglable }
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
                if (-not $e.enabled -and -not (Test-Togglable $e)) { continue }
                $d = Discover-Extension $e $gooseBin
                $exts += $d.ext
                foreach ($r in $d.tools) { $tools += $r }
            }
        } catch {}
        [System.Threading.Monitor]::Enter($shared.SyncRoot)
        try { $shared.exts = $exts; $shared.tools = $tools } finally { [System.Threading.Monitor]::Exit($shared.SyncRoot) }
        # Sleep until the interval elapses OR a toggle wakes us, whichever comes
        # first, so a flipped extension is re-handshaked immediately instead of
        # waiting out the refresh interval.
        [void]$shared.refreshSignal.WaitOne([timespan]::FromSeconds($refreshSec))
        [void]$shared.refreshSignal.Reset()
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
$snap0 = Get-ProviderSnapshot $CFG $GooseConfigPath   # live from goose's config.yaml (env overrides win)
Write-Host "  model     : $($snap0.model)  via $(if ($snap0.host) { $snap0.host } else { 'config.json backends' })  [provider: $(if ($snap0.provider) { $snap0.provider } else { 'unknown' }), live from goose config.yaml]"
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
    uploadsSubdir = $UploadsSubdir; maxUploadBytes = $MaxUploadBytes; maxUploadMb = $MaxUploadMb
    gooseConfig = $GooseConfigPath; discoveryFns = $DiscoveryFns
    profilesPath = (Join-Path $Here '..\config\profiles.json'); repoRoot = (Split-Path -Parent $Here)
}

# ----------------------------------------------------------------------------
# worker: self-contained (all helpers + accept loop). N copies run concurrently
# so a long chat stream does not block the 20s health polls.
# ----------------------------------------------------------------------------
$worker = {
    param($listener, $S)

    $UTF8 = New-Object System.Text.UTF8Encoding($false)

    Invoke-Expression $S.discoveryFns   # Parse-GooseExtensions / Discover-Extension / Test-Togglable / Set-ExtensionEnabled

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
            if (-not $sup) { $sup = Get-QueryValue $ctx.Request.Url.Query 'token' }
            if ($sup -ne $S.token) { Send-Json $ctx @{ error = 'unauthorized' } 401; return }
        }
        # Get-QueryValue, not Request.QueryString: the latter %-decodes with ContentEncoding
        # (the ANSI codepage when the request has no charset), which mangles non-ASCII filenames.
        $session = Get-QueryValue $ctx.Request.Url.Query 'session'; if (-not $session) { $session = 'web' }
        $name = Safe-Name (Get-QueryValue $ctx.Request.Url.Query 'name')
        $len = [int64]$ctx.Request.ContentLength64
        if ($len -le 0) { Send-Json $ctx @{ error = 'empty body' } 400; return }
        if ($len -gt $S.maxUploadBytes) { Send-Json $ctx @{ error = "file too large (> $($S.maxUploadMb) MB)" } 413; return }
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

    function Handle-Toggle($ctx, $S) {
        if ($S.token) {
            $sup = $ctx.Request.Headers['X-Goose-Token']; if (-not $sup) { $sup = Get-QueryValue $ctx.Request.Url.Query 'token' }
            if ($sup -ne $S.token) { Send-Json $ctx @{ error = 'unauthorized' } 401; return }
        }
        $reader = New-Object System.IO.StreamReader($ctx.Request.InputStream, [System.Text.Encoding]::UTF8)
        $bodyText = $reader.ReadToEnd(); $reader.Close()
        $req = $null; try { if ($bodyText.Trim()) { $req = $bodyText | ConvertFrom-Json } } catch {}
        if ($null -eq $req) { Send-Json $ctx @{ error = 'bad json' } 400; return }
        $extId = if ($req.id) { ([string]$req.id).Trim() } else { '' }
        if (-not $extId -or ($req.enabled -isnot [bool])) { Send-Json $ctx @{ error = 'id (str) and enabled (bool) required' } 400; return }
        $enabled = [bool]$req.enabled
        $match = $null
        foreach ($e in (Parse-GooseExtensions $S.gooseConfig)) { if ($e.id -eq $extId) { $match = $e; break } }
        if ($null -eq $match) { Send-Json $ctx @{ error = 'unknown extension' } 404; return }
        if (-not (Test-Togglable $match)) { Send-Json $ctx @{ error = 'extension not togglable' } 403; return }
        # serialize config writes across worker runspaces (parity with Python _config_write_lock)
        $werr = $null
        [System.Threading.Monitor]::Enter($S.shared.cfgWriteLock)
        try { [void](Set-ExtensionEnabled $S.gooseConfig $extId $enabled) }
        catch { $werr = [string]$_ }
        finally { [System.Threading.Monitor]::Exit($S.shared.cfgWriteLock) }
        if ($werr) { Send-Json $ctx @{ error = $werr } 500; return }
        # Update the snapshot in place -- no handshake on the request path, so
        # enabling an MCP whose backend is down returns immediately instead of
        # blocking this response for the handshake timeout. Editing the existing
        # entry (rather than removing and re-appending it) also keeps the card in
        # its config order in the sidebar. The real tool list is filled in by the
        # discoverer, which we wake below (parity with Python's async refresh).
        $shared = $S.shared
        [System.Threading.Monitor]::Enter($shared.SyncRoot)
        try {
            foreach ($x in $shared.exts) {
                if ($x.id -ne $extId) { continue }
                $x.enabled = $enabled
                $x.count   = 0
                $x.status  = if ($enabled) { 'checking' } else { 'disabled' }
            }
            $shared.tools = @($shared.tools | Where-Object { $_.group -ne $extId })
        } finally { [System.Threading.Monitor]::Exit($shared.SyncRoot) }
        [void]$shared.refreshSignal.Set()   # discoverer re-handshakes now, off the request path
        Send-Json $ctx @{ ok = $true; id = $extId; enabled = $enabled }
    }

    function Handle-Profiles($ctx, $S) {
        if ($S.token) {
            $sup = $ctx.Request.Headers['X-Goose-Token']; if (-not $sup) { $sup = Get-QueryValue $ctx.Request.Url.Query 'token' }
            if ($sup -ne $S.token) { Send-Json $ctx @{ error = 'unauthorized' } 401; return }
        }
        try { $profiles = Get-AgentProfiles $S.profilesPath }
        catch { Send-Json $ctx @{ error = [string]$_ } 500; return }

        if ($ctx.Request.HttpMethod -eq 'GET') {
            $states = @{}
            foreach ($e in (Parse-GooseExtensions $S.gooseConfig)) { $states[$e.id] = [bool]$e.enabled }
            $list = @($profiles | ForEach-Object {
                @{ name = $_.name; label = $_.label; description = $_.description; enable = @($_.enable) } })
            Send-Json $ctx @{ ok = $true; profiles = $list; active = (Get-ActiveProfileName $profiles $states) }
            return
        }
        $req = $null; $bt = Read-Utf8Body $ctx
        try { if ($bt.Trim()) { $req = $bt | ConvertFrom-Json } } catch {}
        if ($null -eq $req -or $req.action -ne 'apply' -or -not $req.name) {
            Send-Json $ctx @{ error = 'action "apply" and name required' } 400; return
        }
        $result = $null; $err = $null
        [System.Threading.Monitor]::Enter($S.shared.cfgWriteLock)
        try { $result = Invoke-ProfileApply $S.profilesPath $S.gooseConfig $S.workspace $S.repoRoot ([string]$req.name) }
        catch { $err = [string]$_ }
        finally { [System.Threading.Monitor]::Exit($S.shared.cfgWriteLock) }
        if ($err) { Send-Json $ctx @{ error = $err } 400; return }
        # update the sidebar snapshot in place (parity with Handle-Toggle) then wake the discoverer
        $shared = $S.shared
        $enable = @{}
        $prof = $profiles | Where-Object { $_.name -eq $req.name } | Select-Object -First 1
        foreach ($i in $prof.enable) { $enable[[string]$i] = $true }
        [System.Threading.Monitor]::Enter($shared.SyncRoot)
        try {
            foreach ($x in $shared.exts) {
                if ($result.changed -notcontains $x.id) { continue }
                $x.enabled = $enable.ContainsKey($x.id)
                $x.count = 0
                $x.status = if ($x.enabled) { 'checking' } else { 'disabled' }
            }
            $shared.tools = @($shared.tools | Where-Object { $result.changed -notcontains $_.group })
        } finally { [System.Threading.Monitor]::Exit($shared.SyncRoot) }
        [void]$shared.refreshSignal.Set()
        Send-Json $ctx @{ ok = $true; name = $result.name; changed = @($result.changed); warnings = @($result.warnings) }
    }

    function Handle-Schedules($ctx, $S) {
        if ($S.token) {
            $sup = $ctx.Request.Headers['X-Goose-Token']; if (-not $sup) { $sup = Get-QueryValue $ctx.Request.Url.Query 'token' }
            if ($sup -ne $S.token) { Send-Json $ctx @{ error = 'unauthorized' } 401; return }
        }
        $uri = Resolve-SchedulerUri $S.gooseConfig
        try {
            if ($ctx.Request.HttpMethod -eq 'GET') {
                $res = Invoke-SchedulerTool $uri 'sched_list' @{}
                Send-Json $ctx @{ ok = $true; schedules = @($res.schedules) }; return
            }
            $req = $null; $bt = Read-Utf8Body $ctx
            try { if ($bt.Trim()) { $req = $bt | ConvertFrom-Json } } catch {}
            if ($null -eq $req -or -not $req.action) { Send-Json $ctx @{ error = 'action required' } 400; return }
            switch ($req.action) {
                'create'  { $res = Invoke-SchedulerTool $uri 'sched_create' @{ name=$req.name; kind=$req.kind; expr=$req.expr; session=$req.session; prompt=$req.prompt; mode=$req.mode } }
                'update'  { $res = Invoke-SchedulerTool $uri 'sched_update' @{ id=$req.id; fields=$req.fields } }
                'delete'  { $res = Invoke-SchedulerTool $uri 'sched_delete' @{ id=$req.id } }
                'toggle'  { $res = Invoke-SchedulerTool $uri (if ($req.enabled) { 'sched_resume' } else { 'sched_pause' }) @{ id=$req.id } }
                'run-now' { $res = Invoke-SchedulerTool $uri 'sched_run_now' @{ id=$req.id } }
                'history' { $res = Invoke-SchedulerTool $uri 'sched_history' @{ id=$req.id } }
                default   { Send-Json $ctx @{ error = "unknown action: $($req.action)" } 400; return }
            }
            Send-Json $ctx @{ ok = $true; result = $res }
        } catch {
            Send-Json $ctx @{ error = "scheduler offline: $_" } 502
        }
    }

    function Build-Health($S) {
        $cfg = $S.cfg
        $snap = Get-ProviderSnapshot $cfg $S.gooseConfig   # live: env > goose config.yaml > config.json fallback
        $backends = @()
        foreach ($b in $cfg.backends) {
            $role = [string]$b.role
            # chat/ollama rows follow goose's live OPENAI_HOST/OLLAMA_HOST; other
            # roles (embed, ...) keep their config.json URL.
            $u = ''
            if ($role -and $snap.hosts[$role]) { $u = [string]$snap.hosts[$role] }
            if (-not $u) { $u = [string]$b.url }
            $u = $u.TrimEnd('/')
            $hp = if ($b.health_path) { [string]$b.health_path } else { '/' }
            $backends += @{ name = [string]$b.name; detail = $u; ok = (Test-Url ($u + $hp))
                            active = [bool]($role -and $role -eq $snap.active_role) }   # goose's current provider
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
        $dispHost = if ($snap.host) { [string]$snap.host } else { Get-ChatUrl $cfg }   # config.json fallback if config.yaml unreadable
        $providerStr = if ($dispHost) { "$($snap.label) @ $dispHost" } else { [string]$snap.label }
        return @{
            ok = $true; version = $S.gooseVer; model = [string]$snap.model
            provider = $providerStr; provider_name = [string]$snap.provider
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
                $idle = 0
                while (-not $task.Wait(1000)) {
                    if ((Get-Date) -gt $deadline) { try { if (-not $proc.HasExited) { $proc.Kill() } } catch {}; $killed = $true; break }
                    # keepalive: goose can stay silent 100s+ during tool runs; ping every ~5s so
                    # bytes keep flowing under even aggressive mobile/cellular/VPN (Tailscale) NAT
                    # idle timeouts (~10-15s) that a slower ping would miss.
                    $idle++
                    if ($idle % 5 -eq 0) { $alive = Emit $out @{ type = 'ping' }; if (-not $alive) { break } }
                }
                if ($killed -or -not $alive) { break }
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
            if (-not $supplied) { $supplied = Get-QueryValue $ctx.Request.Url.Query 'token' }
            if ($supplied -ne $S.token) { Send-Json $ctx @{ error = 'unauthorized' } 401; return }
        }
        # JSON is UTF-8 by definition (RFC 8259). Request.ContentEncoding must NOT be trusted here:
        # the browser sends "application/json" with no charset, and .NET then falls back to the
        # system ANSI codepage, which turns a Chinese message into mojibake before goose ever sees it.
        $bodyText = Read-Utf8Body $ctx
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
                elseif ($path -eq '/api/schedules') { Handle-Schedules $ctx $S }
                elseif ($path -eq '/api/profiles') { Handle-Profiles $ctx $S }
                else { Send-Json $ctx @{ error = 'not found' } 404 }
            } elseif ($req.HttpMethod -eq 'POST' -and $path -eq '/api/chat') {
                $streamed = $true; Handle-Chat $ctx $S
            } elseif ($req.HttpMethod -eq 'POST' -and $path -eq '/api/upload') {
                Handle-Upload $ctx $S
            } elseif ($req.HttpMethod -eq 'POST' -and $path -eq '/api/extensions/toggle') {
                Handle-Toggle $ctx $S
            } elseif ($req.HttpMethod -eq 'POST' -and $path -eq '/api/schedules') {
                Handle-Schedules $ctx $S
            } elseif ($req.HttpMethod -eq 'POST' -and $path -eq '/api/profiles') {
                Handle-Profiles $ctx $S
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
