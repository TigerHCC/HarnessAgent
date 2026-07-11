# Windows Update-History MCP — Design Spec

> Date: 2026-07-11 · Status: approved → implementation
> Location: `HarnessAgent/mcp/windows_winupdate/` · Boundary: never modifies `PersonalKnowledge-GB10`
> Twelfth diagnostic MCP (tier-2, final). Rationale: `docs/windows-diagnostic-mcp-candidates.md`
> (`winupdate-history`, value 4). Systems very often break right after a Windows Update — this correlates
> the problem onset with what was installed, and explains *why* an update failed.

## 1. Goal
- **What installed right before the problem started** — full WU history (date, title, KB, operation,
  result), so a crash/slowdown can be correlated with an update's install time.
- **Why an update keeps failing** — the failure result + HRESULT, decoded to human meaning (a table the
  shell has no access to). Full history incl. failures is only available via the **WUA COM API**
  (`Microsoft.Update.Session` → `QueryHistory`); `Get-HotFix`/QFE misses most of it.
- **Is a CU stuck pending / rolling back** — reboot-pending / pending-file-rename state (a classic cause
  of update-loop instability) + the true patch level (OS build.UBR).

## 2. Architecture
```
  goose (USER) ──streamable_http──▶ 127.0.0.1:8788/mcp
   └ extension: winupdate               │
                                        ▼
                winupdate_mcp_server.py  (READ-ONLY)
                  └ winupdate.py  (WUA COM QueryHistory + Get-HotFix via a PowerShell subprocess;
                                   registry pending-state reads; curated WU/CBS HRESULT table. no MCP deps)
```
Bind `127.0.0.1:8788`, streamable HTTP (FastMCP). Read-only — only *queries* WU history / hotfixes /
registry; nothing is installed, uninstalled, or hidden. The WUA COM call goes through a short PowerShell
subprocess (avoids COM apartment-threading issues inside the FastMCP worker threads). Elevation not
required for QueryHistory / Get-HotFix.

## 3. Ground truth (verified this box)
- `Microsoft.Update.Session.CreateUpdateSearcher().QueryHistory(0,n)` → entries with `Date`, `Title`,
  `Operation` (1 Install / 2 Uninstall), `ResultCode` (2 Succeeded / 3 SucceededWithErrors / 4 Failed /
  5 Aborted), `HResult`. 124 entries; **4 real failures surfaced** (Intel driver 0x8024200B, MS.UI.Xaml
  0x80240034).
- `Get-HotFix` → installed KBs + install date + type. `HKLM\...\Component Based Servicing\RebootPending`,
  `...\WindowsUpdate\Auto Update\RebootRequired`, `Session Manager\PendingFileRenameOperations` → pending
  state. `CurrentVersion\CurrentBuild`+`UBR` → true patch level (26200.8655).

## 4. Tool surface (5)
- `update_history(max=100, failures_only=False)` → `[{date, title, kb, operation, result, failed,
  hresult, hresult_name, hresult_meaning}]`, newest first. `failures_only` = just the failed installs.
- `installed_updates(max=200)` → `[{kb, type, installed_on, installed_by}]` from Get-HotFix.
- `pending_state()` → `{reboot_pending, reboot_pending_cbs, reboot_required_wu, pending_file_renames,
  os_build}`.
- `hresult_decode(code)` → `{code, name, meaning}` for a WU/CBS HRESULT.
- `winupdate_health()` → `{is_admin, wua_ok, history_count, reboot_pending, hresult_table_size}`.

Every tool returns a structured `{error:…}`, never raises. Caps observable.

## 5. Safety
Read-only: QueryHistory / Get-HotFix / registry reads only — no update is installed / uninstalled /
hidden / re-run. `code` is validated in the HRESULT decoder. No file writes at all (no baseline needed).

## 6. Files
`winupdate_mcp_server.py` (FastMCP, 5 tools) · `winupdate.py` · `start_winupdate_mcp.ps1` /
`install_task.ps1` / `uninstall_task.ps1` (Scheduled Task `Winupdate-MCP`, 8788) · `requirements.txt`
(mcp, pytest) · `README.md` · `tests/`.

## 7. goose extension
```yaml
  winupdate:
    type: streamable_http
    bundled: false
    name: winupdate
    enabled: true
    uri: http://127.0.0.1:8788/mcp
    headers: {}
    env_keys: []
    timeout: 120
    description: Windows Update history + failure HRESULTs + pending-reboot state via local MCP server (127.0.0.1:8788)
```

## 8. Out of scope (YAGNI)
CBS.log deep parsing (hundreds of MB — a future enhancement). Triggering scans / installs / rollbacks.
WSUS/SCCM server-side history. Component-store repair (that's a `crash`/DISM concern).

## 9. Open risks
- WUA COM through PowerShell: a slow COM call → 60 s timeout, returns a structured error.
- HRESULT table is curated (common WU/CBS codes); unknown codes return the raw hex with name/meaning null.
- `Get-HotFix` InstalledOn is date-only (formatted as the local date, no UTC shift to avoid off-by-one).
