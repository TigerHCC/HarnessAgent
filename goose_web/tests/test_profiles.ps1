# goose_web/tests/test_profiles.ps1 -- run: powershell -NoProfile -File goose_web/tests/test_profiles.ps1
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here '..\mcp_toggle.ps1')          # Set-ExtensionEnabled dependency
. (Join-Path $here '..\profiles_helpers.ps1')
$tmp = Join-Path $env:TEMP ("prof_test_" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path (Join-Path $tmp 'recipes') | Out-Null
New-Item -ItemType Directory -Path (Join-Path $tmp 'ws') | Out-Null
try {
    # fixture: 2 profiles over 3 managed extensions (a,b,c) + unmanaged (z)
    'RECIPE-ALPHA' | Set-Content (Join-Path $tmp 'recipes\alpha.md') -Encoding UTF8
    'RECIPE-BETA'  | Set-Content (Join-Path $tmp 'recipes\beta.md') -Encoding UTF8
    $profJson = Join-Path $tmp 'profiles.json'
    @'
[ {"name":"alpha","label":"A","description":"d","enable":["a","b"],"recipe":"recipes/alpha.md"},
  {"name":"beta","label":"B","description":"d","enable":["b","c"],"recipe":"recipes/beta.md"} ]
'@ | Set-Content $profJson -Encoding UTF8
    $cfg = Join-Path $tmp 'config.yaml'
    @'
GOOSE_PROVIDER: openai
extensions:
  a:
    type: builtin
    enabled: false
  b:
    type: builtin
    enabled: true
  c:
    type: builtin
    enabled: true
  z:
    type: builtin
    enabled: true
'@ | Set-Content $cfg -Encoding UTF8

    # 1) parse + managed set
    $profiles = Get-AgentProfiles $profJson
    if ($profiles.Count -ne 2) { throw 'parse failed' }
    $managed = Get-ManagedExtIds $profiles
    if (($managed | Sort-Object) -join ',' -ne 'a,b,c') { throw "managed set wrong: $managed" }

    # 2) active detection: current enabled managed = b,c -> beta
    $states = @{ a = $false; b = $true; c = $true; z = $true }
    if ((Get-ActiveProfileName $profiles $states) -ne 'beta') { throw 'active should be beta' }
    $states.c = $false
    if ((Get-ActiveProfileName $profiles $states) -ne 'custom') { throw 'active should be custom' }

    # 3) apply alpha: a,b enabled; c disabled; z untouched; goosehints written; backup exists
    $r = Invoke-ProfileApply $profJson $cfg (Join-Path $tmp 'ws') $tmp 'alpha'
    if (-not $r.ok) { throw 'apply failed' }
    $out = Get-Content -Raw $cfg
    if ($out -notmatch '(?ms)^  a:.*?enabled: true') { throw 'a not enabled' }
    if ($out -notmatch '(?ms)^  c:.*?enabled: false') { throw 'c not disabled' }
    if ($out -notmatch '(?ms)^  z:.*?enabled: true') { throw 'z was touched' }
    $gh = Get-Content -Raw (Join-Path $tmp 'ws\.goosehints')
    if ($gh -notmatch 'profile: alpha' -or $gh -notmatch 'RECIPE-ALPHA') { throw 'goosehints wrong' }
    if (-not (Test-Path "$cfg.bak-profile")) { throw 'backup missing' }

    # 4) unknown profile: throws, config untouched
    $before = Get-Content -Raw $cfg
    $threw = $false
    try { [void](Invoke-ProfileApply $profJson $cfg (Join-Path $tmp 'ws') $tmp 'nope') } catch { $threw = $true }
    if (-not $threw) { throw 'unknown name should throw' }
    if ((Get-Content -Raw $cfg) -ne $before) { throw 'config modified on unknown name' }

    Write-Host '[OK] profiles helpers pass' -ForegroundColor Green
} finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
