# mcp/markitdown/tests/test_register.ps1 -- run: powershell -NoProfile -File mcp/markitdown/tests/test_register.ps1
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $here '..\register_goose_extension.ps1'
$tmp = Join-Path $env:TEMP ("mkd_reg_test_" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    # 1) config WITH extensions: -> block added once
    $cfgPath = Join-Path $tmp 'config.yaml'
    "GOOSE_PROVIDER: openai`nextensions:`n  developer:`n    type: builtin" | Set-Content -Path $cfgPath -Encoding UTF8
    & powershell -NoProfile -File $script -ConfigPath $cfgPath | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "expected exit 0 on add, got $LASTEXITCODE" }
    $out = Get-Content -Raw $cfgPath
    if ($out -notmatch "(?m)^\s{2}markitdown\s*:") { throw 'markitdown block not added' }
    if ($out -notmatch "uri: http://127\.0\.0\.1:8794/mcp") { throw 'uri wrong or missing' }
    if (-not (Test-Path "$cfgPath.bak-markitdown")) { throw 'backup not created' }
    # 2) idempotent: second run adds nothing
    & powershell -NoProfile -File $script -ConfigPath $cfgPath | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "expected exit 0 on already-present, got $LASTEXITCODE" }
    $out2 = Get-Content -Raw $cfgPath
    $count = ([regex]::Matches($out2, "(?m)^\s{2}markitdown\s*:")).Count
    if ($count -ne 1) { throw "expected exactly 1 markitdown block, got $count" }
    # 3) config WITHOUT extensions: -> exit 1, file untouched
    $bare = Join-Path $tmp 'bare.yaml'
    "GOOSE_PROVIDER: openai" | Set-Content -Path $bare -Encoding UTF8
    & powershell -NoProfile -File $script -ConfigPath $bare | Out-Null
    if ($LASTEXITCODE -ne 1) { throw "expected exit 1 without extensions:, got $LASTEXITCODE" }
    if ((Get-Content -Raw $bare) -match "markitdown") { throw 'bare config was modified' }
    Write-Host '[OK] register script tests pass' -ForegroundColor Green
} finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
