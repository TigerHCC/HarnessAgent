# Hidden MCP Launcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start all scheduled MCP servers through a hidden PowerShell process with bounded, per-server stdout and stderr logs.

**Architecture:** A focused launcher owns path validation, 10 MiB single-generation log rotation, and the Python process lifetime. A shared helper builds the quoted PowerShell command line used by both the suite installer and each standalone installer, while all existing Scheduled Task triggers and principals remain unchanged.

**Tech Stack:** Windows PowerShell 5.1, ScheduledTasks module, Python 3, pytest

## Global Constraints

- Keep `AtLogOn`, the current-user principal, `LogonType Interactive`, and each existing `Highest` or `Limited` run level.
- Run PowerShell with `-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden`.
- Store separate stdout and stderr logs under `logs/mcp/`.
- Rotate a log only when it is larger than 10 MiB and retain exactly one `.1` generation.
- Do not introduce `AtStartup`, `SYSTEM`, S4U, password logon, a Windows service, or a new dependency.

---

### Task 1: Hidden Launcher and Log Rotation

**Files:**
- Create: `scripts/start_mcp_hidden.ps1`
- Create: `tests/test_mcp_hidden_launcher.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `-PythonPath`, `-ServerPath`, `-WorkingDirectory`, `-Name`, and `-LogDirectory` string parameters.
- Produces: `<name>.stdout.log`, `<name>.stderr.log`, optional `.1` files, and the child Python exit code.

- [ ] **Step 1: Write failing subprocess tests**

Create `tests/test_mcp_hidden_launcher.py`. Resolve `powershell` or `pwsh`, create a temporary Python server that writes distinct stdout/stderr lines, and invoke the missing launcher with paths containing spaces. Assert exit code zero, separated output, append behavior, over-10-MiB rotation, and non-zero failure for a missing server.

```python
ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "start_mcp_hidden.ps1"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")

def run_launcher(python, server, working, name, logs):
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(LAUNCHER), "-PythonPath", str(python), "-ServerPath", str(server),
         "-WorkingDirectory", str(working), "-Name", name, "-LogDirectory", str(logs)],
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python -m pytest tests/test_mcp_hidden_launcher.py -q`

Expected: FAIL because `scripts/start_mcp_hidden.ps1` does not exist.

- [ ] **Step 3: Implement the minimal launcher**

Create a `[CmdletBinding()]` script with mandatory parameters, `$ErrorActionPreference = "Stop"`, and a `Rotate-Log` function. Resolve Python, server, and working directory with `Resolve-Path -LiteralPath`; create the log directory; reject an MCP name outside `^[A-Za-z0-9._-]+$`; rotate files whose length is greater than `10MB`; set `PYTHONIOENCODING=utf-8`; execute Python synchronously with `1>>` and `2>>`; and `exit` with `$LASTEXITCODE`.

```powershell
function Rotate-Log([string]$Path) {
    if ((Test-Path -LiteralPath $Path) -and
        (Get-Item -LiteralPath $Path).Length -gt 10MB) {
        Move-Item -LiteralPath $Path -Destination "$Path.1" -Force
    }
}
```

Add `/logs/mcp/` to `.gitignore`.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run: `python -m pytest tests/test_mcp_hidden_launcher.py -q`

Expected: all launcher tests PASS.

- [ ] **Step 5: Commit the launcher**

```powershell
git add scripts/start_mcp_hidden.ps1 tests/test_mcp_hidden_launcher.py .gitignore
git commit -m "feat(mcp): add hidden server launcher"
```

---

### Task 2: Shared Scheduled Task Action Builder

**Files:**
- Create: `scripts/mcp_task_helpers.ps1`
- Modify: `tests/test_mcp_hidden_launcher.py`

**Interfaces:**
- Consumes: launcher, Python, server, working-directory, MCP name, and log-directory paths.
- Produces: `New-McpScheduledTaskAction` returning a ScheduledTaskAction and `Start-McpHiddenServer` returning the started process.

- [ ] **Step 1: Write failing helper contract tests**

Add tests that dot-source the helper in PowerShell and use `New-McpLauncherArguments` with paths containing spaces. Assert the returned command contains all required flags, quotes each value, and includes `-WindowStyle Hidden` only in the PowerShell invocation rather than passing it to the launcher. Add a source assertion that direct `python.exe` is not used as the scheduled action executable.

- [ ] **Step 2: Run the helper tests and verify RED**

Run: `python -m pytest tests/test_mcp_hidden_launcher.py -q`

Expected: FAIL because `scripts/mcp_task_helpers.ps1` does not exist.

- [ ] **Step 3: Implement argument and action helpers**

Create functions that reject embedded double quotes, wrap every value in double quotes, and return this exact switch sequence:

```text
-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "<launcher>" -PythonPath "<python>" -ServerPath "<server>" -WorkingDirectory "<dir>" -Name "<name>" -LogDirectory "<logs>"
```

`New-McpScheduledTaskAction` calls `New-ScheduledTaskAction -Execute $PowerShellPath -Argument $arguments -WorkingDirectory $WorkingDirectory`. `Start-McpHiddenServer` calls `Start-Process -FilePath $PowerShellPath -ArgumentList $arguments -WindowStyle Hidden -PassThru`.

- [ ] **Step 4: Run the helper tests and verify GREEN**

Run: `python -m pytest tests/test_mcp_hidden_launcher.py -q`

Expected: all helper and launcher tests PASS.

- [ ] **Step 5: Commit the helper**

```powershell
git add scripts/mcp_task_helpers.ps1 tests/test_mcp_hidden_launcher.py
git commit -m "feat(mcp): centralize hidden task actions"
```

---

### Task 3: Integrate Every Installer Path

**Files:**
- Modify: `setup_mcp_servers.ps1`
- Modify: `mcp/dtm_sdk/install_task.ps1`
- Modify: `mcp/windows_crash/install_task.ps1`
- Modify: `mcp/windows_disk/install_task.ps1`
- Modify: `mcp/windows_drift/install_task.ps1`
- Modify: `mcp/windows_eventlog/install_task.ps1`
- Modify: `mcp/windows_exec/install_task.ps1`
- Modify: `mcp/windows_filterstack/install_task.ps1`
- Modify: `mcp/windows_memstate/install_task.ps1`
- Modify: `mcp/windows_netconn/install_task.ps1`
- Modify: `mcp/windows_obsidian/install_task.ps1`
- Modify: `mcp/windows_perfmon/install_task.ps1`
- Modify: `mcp/windows_procinspect/install_task.ps1`
- Modify: `mcp/windows_srum/install_task.ps1`
- Modify: `mcp/windows_winupdate/install_task.ps1`
- Modify: `tests/test_mcp_hidden_launcher.py`

**Interfaces:**
- Consumes: `New-McpScheduledTaskAction` and `Start-McpHiddenServer` from Task 2.
- Produces: all 14 Scheduled Tasks using the hidden launcher while retaining their existing trigger and principal properties.

- [ ] **Step 1: Write failing integration source tests**

Enumerate all manifest directories, read their `install_task.ps1`, and assert each script dot-sources `scripts/mcp_task_helpers.ps1`, references `scripts/start_mcp_hidden.ps1`, calls `New-McpScheduledTaskAction`, and contains its manifest name. Assert no installer contains `New-ScheduledTaskAction -Execute $py`. For `setup_mcp_servers.ps1`, assert both task registration and immediate background start use the shared helpers. Retain explicit assertions for `-AtLogOn`, `-LogonType Interactive`, and Obsidian's `RunLevel Limited`.

- [ ] **Step 2: Run integration tests and verify RED**

Run: `python -m pytest tests/test_mcp_hidden_launcher.py -q`

Expected: FAIL because installers still execute Python directly.

- [ ] **Step 3: Update the suite installer**

After resolving `$here`, dot-source `scripts\mcp_task_helpers.ps1`, define the launcher, PowerShell executable, and `logs\mcp` directory, and replace direct Scheduled Task action creation with:

```powershell
$action = New-McpScheduledTaskAction -PowerShellPath $powershell -LauncherPath $launcher `
    -PythonPath $py -ServerPath $server -WorkingDirectory $dir -Name $m.name -LogDirectory $logRoot
```

Replace the inline hidden `Start-Process powershell ... & python` command with `Start-McpHiddenServer` using the same arguments.

- [ ] **Step 4: Update all standalone installers**

In every `install_task.ps1`, resolve the repository root from `$here`, dot-source the helper, and build the action through `New-McpScheduledTaskAction`. Preserve the existing task name, trigger, principal, settings, admin check, and status messages. Use the manifest's lowercase `name` for log filenames.

- [ ] **Step 5: Run integration tests and verify GREEN**

Run: `python -m pytest tests/test_mcp_hidden_launcher.py tests/test_mcp_manifest.py -q`

Expected: all tests PASS.

- [ ] **Step 6: Parse every changed PowerShell file**

Run a PowerShell parser loop over `setup_mcp_servers.ps1`, both shared scripts, and every `mcp/*/install_task.ps1`.

Expected: zero parser errors.

- [ ] **Step 7: Commit installer integration**

```powershell
git add setup_mcp_servers.ps1 mcp/*/install_task.ps1 tests/test_mcp_hidden_launcher.py
git commit -m "feat(mcp): launch scheduled servers without consoles"
```

---

### Task 4: Documentation and Full Verification

**Files:**
- Modify: `README.md`
- Modify: `RUN.md`
- Modify: `mcp/README.md`
- Modify: `docs/SETUP_GUIDE.md`
- Modify: `docs/MODULE_RELATIONSHIPS.md`

**Interfaces:**
- Consumes: final launcher paths and logging behavior from Tasks 1-3.
- Produces: install, operation, troubleshooting, and architecture documentation matching runtime behavior.

- [x] **Step 1: Update user-facing documentation**

Document that MCP tasks still start at user logon as the current user but use a hidden PowerShell launcher. Add `logs/mcp/<name>.stdout.log`, `<name>.stderr.log`, 10 MiB `.1` rotation, and PowerShell examples using `Get-Content -Wait` for troubleshooting. Update the Mermaid module relationship diagram to show Scheduled Task to hidden launcher to Python MCP and log files.

- [x] **Step 2: Run Markdown consistency searches**

Run:

```powershell
rg -n "python.exe|console|AtLogOn|Scheduled Task|logs/mcp|start_mcp_hidden" -g "*.md"
```

Expected: current operational docs describe the hidden launcher; historical plans remain unchanged.

- [x] **Step 3: Run the approved split repository test suites**

Run the root suite:

```powershell
python -m pytest tests -q
```

Result: 121 passed.

Then, from each MCP directory that contains `tests/`, run:

```powershell
python -m pytest tests -q
```

Result across all 14 MCP directories: 297 passed, 10 skipped. The monolithic
`python -m pytest -q` command is not used for completion because duplicate top-level test module names
produce 19 collection errors when those directories are collected together.

- [x] **Step 4: Run installer dry-path validation**

Run: `powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .\setup_mcp_servers.ps1 -SkipDeps -SkipTasks -SkipConfig -SkipSysmon -NoStart`

Expected: exit code 0 without modifying Scheduled Tasks or starting servers.

Resolved by `aff4f97`: the command exits 0, reports `Skipping Scheduled Task registration (-SkipTasks)`,
and performs no watchdog installation, Scheduled Task registration, or MCP start.

- [x] **Step 5: Inspect final diff and commit documentation**

Run `git diff --check`, inspect `git diff --stat` and `git status --short`, then commit only intended files:

```powershell
git add README.md RUN.md mcp/README.md docs/SETUP_GUIDE.md docs/MODULE_RELATIONSHIPS.md docs/superpowers/plans/2026-07-16-hidden-mcp-launcher.md
git commit -m "docs: explain hidden MCP startup logs"
```
