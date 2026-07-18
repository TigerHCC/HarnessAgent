# goose_web/tests/test_schedules.ps1  -- run with:  powershell -File goose_web/tests/test_schedules.ps1
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
# Load just the discovery function block from server.ps1 by extracting the $DiscoveryFns here-string is
# brittle; instead dot-source a tiny extraction: we test Merge-ConfirmArgs + result parsing in isolation.
. (Join-Path $here '..\schedules_helpers_under_test.ps1')

# 1) Merge-ConfirmArgs adds the token without mutating the caller's hashtable identity semantics
$a = @{ id = 'x' }
$merged = Merge-ConfirmArgs $a 'tok123'
if ($merged.confirm_token -ne 'tok123' -or $merged.id -ne 'x') { throw 'Merge-ConfirmArgs failed' }

# 2) Parse-McpResult reads structuredContent first, then content[0].text JSON
$structured = '{"result":{"structuredContent":{"ok":true,"count":2}}}' | ConvertFrom-Json
if ((Parse-McpResult $structured).count -ne 2) { throw 'structuredContent parse failed' }
$textual = '{"result":{"content":[{"type":"text","text":"{\"requires_confirmation\":true,\"confirm_token\":\"t9\"}"}]}}' | ConvertFrom-Json
$p = Parse-McpResult $textual
if (-not $p.requires_confirmation -or $p.confirm_token -ne 't9') { throw 'content text parse failed' }

Write-Host '[OK] schedules helpers pass' -ForegroundColor Green
