# MarkItDown MCP Mount Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mount Microsoft's official `markitdown-mcp` server on `127.0.0.1:8794` as a manifest-external service: shim + scheduled-task scaffolding + an idempotent goose-config registration script.

**Architecture:** No custom MCP code — a ~10-line shim makes the official package launchable by the shared hidden launcher (`scripts/start_mcp_hidden.ps1` needs a `.py` ServerPath). A standalone `register_goose_extension.ps1` adds the config.yaml extension block (mirroring `setup_mcp_servers.ps1`'s block format) because this service is outside the canonical manifest.

**Tech Stack:** `markitdown-mcp` (pip), Python 3 shim + pytest, PowerShell 5.1 scripts.

## Global Constraints

- Loopback only: the shim pins `--http --host 127.0.0.1 --port 8794`. Scheduled Task name EXACTLY `MarkItDown-MCP`, AtLogOn, RunLevel **Limited**, via `scripts\start_mcp_hidden.ps1` with `-Name "markitdown"`.
- ZERO changes to `config/mcp_servers.json`, `setup_mcp_servers.ps1`, `test_mcp_servers.ps1`/`scripts/test_mcp_servers.py`, `tools/mcp_watchdog/*`, and goose_web. The ONLY file outside `mcp/markitdown/` touched is `mcp/README.md` (one short note).
- The register script must be idempotent, back up config.yaml to `<config>.bak-markitdown` before writing, and never run during tests against the LIVE config (tests use a temp copy).
- Do NOT run `install_task.ps1` or modify the live config.yaml during implementation — deployment is a post-merge manual step.
- Branch `feature/markitdown-mcp`; commit there; do not push.
- Every commit message body ends with the repo's two trailers:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_013Wm8BeurMKFZK6TgvFhLjE`. (Omitted below — add them.)

---

## File Structure

- Create `mcp/markitdown/requirements.txt`
- Create `mcp/markitdown/run_markitdown_mcp.py` (shim) + `mcp/markitdown/conftest.py` + `mcp/markitdown/tests/test_shim.py`
- Create `mcp/markitdown/install_task.ps1`, `mcp/markitdown/uninstall_task.ps1`
- Create `mcp/markitdown/register_goose_extension.ps1` + `mcp/markitdown/tests/test_register.ps1`
- Create `mcp/markitdown/README.md`; Modify `mcp/README.md` (one note)

---

## Task 1: Shim + scaffolding + README

**Files:**
- Create: `mcp/markitdown/requirements.txt`, `run_markitdown_mcp.py`, `conftest.py`, `tests/test_shim.py`, `install_task.ps1`, `uninstall_task.ps1`, `README.md`

**Interfaces:**
- Produces: `run_markitdown_mcp.ARGS` (list), `_resolve_main() -> callable`, `main()` — Task 2 does not consume these; they are the launcher entry.

- [ ] **Step 1: Install the package and discover the real entry point**

```powershell
python -m pip install markitdown-mcp
python -c "import markitdown_mcp; print(markitdown_mcp.__file__)"
python -c "from markitdown_mcp import main; print(main)"          # candidate A
python -c "from markitdown_mcp.__main__ import main; print(main)" # candidate B
```
Note which candidate resolves (at least one must). Record the installed version (`python -m pip show markitdown-mcp`).

- [ ] **Step 2: Write the failing tests**

```python
# mcp/markitdown/conftest.py
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
```

```python
# mcp/markitdown/tests/test_shim.py
import sys
import run_markitdown_mcp as shim


def test_args_pin_http_loopback_8794():
    assert shim.ARGS[0] == "markitdown-mcp"
    assert "--http" in shim.ARGS
    assert shim.ARGS[shim.ARGS.index("--host") + 1] == "127.0.0.1"
    assert shim.ARGS[shim.ARGS.index("--port") + 1] == "8794"


def test_resolve_main_returns_callable():
    assert callable(shim._resolve_main())          # requires markitdown-mcp installed


def test_main_sets_argv_then_calls_entry(monkeypatch):
    seen = {}
    def fake_main():
        seen["argv"] = list(sys.argv)
    monkeypatch.setattr(shim, "_resolve_main", lambda: fake_main)
    shim.main()
    assert seen["argv"] == shim.ARGS               # argv set BEFORE the entry ran
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd mcp/markitdown && python -m pytest tests/ -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'run_markitdown_mcp'`.

- [ ] **Step 4: Write the shim**

```python
# mcp/markitdown/run_markitdown_mcp.py
"""Launch shim for Microsoft's official markitdown-mcp server (manifest-external, 127.0.0.1:8794).

Exists only because the shared hidden launcher (scripts/start_mcp_hidden.ps1) takes a .py ServerPath;
it sets argv for streamable-http on loopback:8794 and hands off to the official entry point. All
behavior (the convert_to_markdown tool) is the official package's, unmodified.
"""
import sys

ARGS = ["markitdown-mcp", "--http", "--host", "127.0.0.1", "--port", "8794"]


def _resolve_main():
    try:
        from markitdown_mcp import main as md_main            # console-script target
    except ImportError:
        from markitdown_mcp.__main__ import main as md_main   # fallback layout
    return md_main


def main():
    sys.argv = list(ARGS)
    _resolve_main()()


if __name__ == "__main__":
    main()
```
If Step 1 showed only ONE candidate resolves, keep the try/except anyway (harmless, version-proof). If the entry needs arguments passed differently (e.g. `main(argv)`), adapt the shim AND the third test to match reality and note the adaptation in your report.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd mcp/markitdown && python -m pytest tests/ -v`
Expected: 3 passed.

- [ ] **Step 6: Live smoke test** — start the shim, expect FastMCP-style rejection of a bare GET, then stop it:

```powershell
Start-Process -WindowStyle Hidden python (Resolve-Path mcp/markitdown/run_markitdown_mcp.py)
Start-Sleep 4
try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8794/mcp -TimeoutSec 4 } catch { $_.Exception.Response.StatusCode.value__ }
# expect 406, 400, or 405 (server alive, rejecting bare GET); then stop the python process you started
```
Find and stop it: `Get-CimInstance Win32_Process -Filter "Name='python.exe'"` filtered on `run_markitdown_mcp` in CommandLine → `Stop-Process -Id <pid>`. Do not leave it running.

- [ ] **Step 7: Write `requirements.txt`**

```
markitdown-mcp
pytest>=8.0
```

- [ ] **Step 8: Write `install_task.ps1`** (mirror `mcp/dtm_download/install_task.ps1`; substitutions only)

```powershell
# Registers a Scheduled Task that runs the official markitdown-mcp server at logon, UNELEVATED
# (RunLevel Limited). Registering a Scheduled Task itself requires Administrator (a Windows requirement).
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent (Split-Path -Parent $here)
$launcher = Join-Path $repoRoot "scripts\start_mcp_hidden.ps1"
. (Join-Path $repoRoot "scripts\mcp_task_helpers.ps1")
$powershell = (Get-Command powershell).Source
$logRoot = Join-Path $repoRoot "logs\mcp"
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) { Write-Host "[X] Run this elevated to REGISTER the task (the server itself runs unelevated)." -ForegroundColor Red; exit 1 }

$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { Write-Host "[X] Python 3 not found on PATH." -ForegroundColor Red; exit 1 }
$server = Join-Path $here "run_markitdown_mcp.py"
$action = New-McpScheduledTaskAction -PowerShellPath $powershell -LauncherPath $launcher `
    -PythonPath $py -ServerPath $server -WorkingDirectory $here -Name "markitdown" -LogDirectory $logRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -RunLevel Limited -LogonType Interactive
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "MarkItDown-MCP" -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "[OK] Registered scheduled task 'MarkItDown-MCP' (UNELEVATED, at logon)." -ForegroundColor Green
Write-Host "     Start now: Start-ScheduledTask -TaskName MarkItDown-MCP" -ForegroundColor Cyan
Write-Host "     Remove:    .\uninstall_task.ps1" -ForegroundColor Cyan
```

- [ ] **Step 9: Write `uninstall_task.ps1`**

```powershell
# Removes the MarkItDown MCP scheduled task.
$ErrorActionPreference = "SilentlyContinue"
Unregister-ScheduledTask -TaskName "MarkItDown-MCP" -Confirm:$false
Write-Host "[OK] Removed scheduled task 'MarkItDown-MCP' (if it existed)." -ForegroundColor Green
```

- [ ] **Step 10: Write `README.md`** — follow `mcp/dtm_download/README.md`'s structure. Must cover: what `convert_to_markdown(uri)` does + supported formats; that this is the OFFICIAL Microsoft server mounted manifest-external (and why: no health tool → outside watchdog/batch-test; restarts at next logon only); security notes (loopback/unelevated; `file://` reads any user-readable file, `http(s)://` performs egress — grants nothing the developer extension's shell doesn't already; no confirm-gating for that reason); optional binaries (ffmpeg → audio transcription, exiftool → EXIF; most formats work without them; Azure extras NOT installed); troubleshooting (check `logs/mcp/markitdown.stderr.log` first, port 8794 conflicts); how to register via `register_goose_extension.ps1` (Task 2).

- [ ] **Step 11: PS parse checks + commit**

```powershell
$null=[ScriptBlock]::Create((Get-Content -Raw mcp/markitdown/install_task.ps1)); '[OK] install parses'
$null=[ScriptBlock]::Create((Get-Content -Raw mcp/markitdown/uninstall_task.ps1)); '[OK] uninstall parses'
```

```bash
git add mcp/markitdown/requirements.txt mcp/markitdown/run_markitdown_mcp.py mcp/markitdown/conftest.py mcp/markitdown/tests/test_shim.py mcp/markitdown/install_task.ps1 mcp/markitdown/uninstall_task.ps1 mcp/markitdown/README.md
git commit -m "feat(markitdown): shim + scaffolding for official markitdown-mcp on 8794"
```

---

## Task 2: goose-config registration script + suite doc note

**Files:**
- Create: `mcp/markitdown/register_goose_extension.ps1`
- Test: `mcp/markitdown/tests/test_register.ps1`
- Modify: `mcp/README.md` (one note)

**Interfaces:**
- Consumes: nothing from Task 1. Produces: `register_goose_extension.ps1 [-ConfigPath <path>]` — idempotent; exit 0 on added/already-present, exit 1 on missing config or missing `extensions:` section.

- [ ] **Step 1: Write the failing test**

```powershell
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `powershell -NoProfile -File mcp/markitdown/tests/test_register.ps1`
Expected: FAIL (script not found).

- [ ] **Step 3: Write `register_goose_extension.ps1`** (block format copied from `setup_mcp_servers.ps1:266-303`)

```powershell
# Adds the markitdown extension to goose's config.yaml (idempotent; backs up first).
# This service is manifest-external, so setup_mcp_servers.ps1 does not manage it -- this script is
# the one-time registration step. Safe to re-run.
param([string]$ConfigPath = (Join-Path $env:APPDATA "Block\goose\config\config.yaml"))
$ErrorActionPreference = "Stop"
if (-not (Test-Path $ConfigPath)) { Write-Host "[X] goose config not found: $ConfigPath" -ForegroundColor Red; exit 1 }
$cfg = Get-Content $ConfigPath -Raw
if ($cfg -notmatch "(?m)^\s*extensions\s*:") { Write-Host "[X] no 'extensions:' section in $ConfigPath -- run setup_goose.ps1 first." -ForegroundColor Red; exit 1 }
if ($cfg -match "(?m)^\s{2}markitdown\s*:") { Write-Host "[OK] markitdown already present -- no change." -ForegroundColor Green; exit 0 }
Copy-Item $ConfigPath "$ConfigPath.bak-markitdown" -Force
$block = @"

  markitdown:
    type: streamable_http
    bundled: false
    name: markitdown
    enabled: true
    uri: http://127.0.0.1:8794/mcp
    headers: {}
    env_keys: []
    timeout: 120
    description: 'Convert documents (PDF, Office, images, audio, HTML, CSV, ZIP, YouTube, EPub) to Markdown via the official Microsoft markitdown-mcp server (manifest-external, 127.0.0.1:8794).'
"@
Add-Content -Path $ConfigPath -Value $block -Encoding UTF8
Write-Host "[OK] Added markitdown extension to $ConfigPath (backup: $ConfigPath.bak-markitdown)" -ForegroundColor Green
```

- [ ] **Step 4: Run test to verify it passes**

Run: `powershell -NoProfile -File mcp/markitdown/tests/test_register.ps1`
Expected: `[OK] register script tests pass`.

- [ ] **Step 5: `mcp/README.md` note** — add a short paragraph (placed after the canonical-suite description, clearly outside the "17 servers" accounting): markitdown is a manifest-external 18th service on 8794 running the official `markitdown-mcp` package; unelevated; not covered by watchdog/batch test; see `mcp/markitdown/README.md`.

- [ ] **Step 6: Full verification + commit**

Run: `cd mcp/markitdown && python -m pytest tests/ -v` (3 passed, unchanged) and the register test again.

```bash
git add mcp/markitdown/register_goose_extension.ps1 mcp/markitdown/tests/test_register.ps1 mcp/README.md
git commit -m "feat(markitdown): idempotent goose-config registration script"
```

---

## Post-merge deployment (manual, NOT part of the plan's tasks)

1. Elevated: `mcp\markitdown\install_task.ps1` → `Start-ScheduledTask MarkItDown-MCP`.
2. `mcp\markitdown\register_goose_extension.ps1` (live config, with backup).
3. Verify: 8794 answers 406/400/405; goose_web sidebar shows the markitdown card; convert one real PDF/DOCX via the agent.

## Self-Review Notes

- Spec coverage: shim/launcher fit (Task 1), scheduled-task scaffolding Limited/AtLogOn (Task 1), README with security + optional binaries (Task 1 Step 10), config registration with backup + idempotence (Task 2), mcp/README note preserving "17 servers" claims (Task 2 Step 5), no suite-file/goose_web changes (only mcp/README.md, allowed by spec), tests for shim and register script, manual acceptance listed post-merge.
- Entry-point uncertainty is handled inside Task 1 (Step 1 discovery + Step 4 adaptation note) rather than left ambiguous.
- Type consistency: `ARGS`/`_resolve_main`/`main` names match between shim and tests; `-ConfigPath` param name matches between script and its test.
