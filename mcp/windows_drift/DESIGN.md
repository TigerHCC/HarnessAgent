# Windows Config-Drift MCP — Design Spec

> Date: 2026-07-11 · Status: approved → implementation
> Location: `HarnessAgent/mcp/windows_drift/` · Boundary: **never modifies `PersonalKnowledge-GB10`**
> Fifth sibling of srum(8777)/eventlog(8778)/crash(8779)/exec(8780). Rationale + ranking:
> `docs/windows-diagnostic-mcp-candidates.md` (candidate #4, value 5).

## 1. Goal
Answer the killer diagnostic question **"what changed on this machine right before the problem started?"**
The shell can only see *now*; this MCP persists point-in-time snapshots to SQLite and diffs them over
time (added / removed / changed), so `what_changed_since(last-good-date)` is one call. Build it **early**
so baselines exist before the next incident.

Tracked configuration surfaces (the ones that actually cause instability / slow boot / background CPU):
- **autoruns** — ASEP: Run/RunOnce (HKLM, HKLM\\Wow6432Node, HKCU), Winlogon Shell/Userinit, Startup folders.
- **services** — every `HKLM\\SYSTEM\\CurrentControlSet\\Services` entry (services *and* drivers): ImagePath,
  Start type, Type, DisplayName.
- **programs** — installed software from the Uninstall keys (HKLM + Wow6432 + HKCU).
- **tasks** — scheduled tasks (on-disk `C:\\Windows\\System32\\Tasks` XML): command, args, enabled.

## 2. Architecture
```
  goose (USER mode) ──streamable_http──▶ 127.0.0.1:8781/mcp
   └ extension: drift                        │
                                             ▼
                    drift_mcp_server.py  (ELEVATED / admin, READ-ONLY vs the system)
                      ├ collectors.py   (4 normalized collectors via winreg + Tasks XML; no MCP deps)
                      └ drift_store.py  (SQLite snapshots + diff; no MCP deps)
```
Runs **elevated** (Services/Tasks need admin). Bind `127.0.0.1:8781`, streamable HTTP (FastMCP). Pure
**stdlib** (`winreg`, `sqlite3`, `xml.etree`, `hashlib`). The only thing it *writes* is its own snapshot
DB (`data/drift.db`, gitignored) — it never modifies any tracked system setting.

## 3. Data model
Each collector yields normalized **items**: `{category, key, name, detail:{...}}` where `key` is unique
within a category (e.g. `HKLM\\...\\Run|OneDrive`, or a service name, or a task path). A `value_hash` =
SHA-1 of the canonical `detail` JSON detects "changed".

SQLite:
```
snapshots(id INTEGER PK, ts TEXT, note TEXT)
items(snapshot_id INT, category TEXT, item_key TEXT, name TEXT, detail_json TEXT, value_hash TEXT)
```

## 4. Tool surface (6)
- `snapshot_now(note=None)` → collect all categories, persist, return `{snapshot_id, ts, counts}`.
- `list_snapshots()` → `[{id, ts, note, total_items, counts}]`, newest first.
- `current(category=None, filter=None, max=200)` → live enumeration (no persist) of the current config.
- `diff(a=None, b=None, category=None)` → compare snapshot `a` vs `b`. Defaults: `a` = latest snapshot,
  `b` = **live now** (ephemeral). Returns `{added:[], removed:[], changed:[{key, name, from, to}]}` with
  per-category counts; caps observable.
- `what_changed_since(ref)` → `ref` = snapshot id or ISO date; diffs the snapshot at/just-before `ref`
  against live now. The headline "what changed since last-good".
- `drift_health()` → `{is_admin, db_path, snapshot_count, live_counts, collectors_ok}`.

Every tool returns a structured `{error:…}`, never raises.

## 5. Collectors (`collectors.py`)
- **autoruns**: Run/RunOnce values under the four hives; Winlogon `Shell`/`Userinit`; Startup-folder
  files (ProgramData + per-user). `key = "<location>|<name>"`, `detail = {location, command}`.
- **services**: enumerate `HKLM\\SYSTEM\\CurrentControlSet\\Services\\*` reading `ImagePath`, `Start`,
  `Type`, `DisplayName`; decode Start (0 Boot…4 Disabled) and Type (1 KernelDriver, 2 FSDriver, 16/32
  service). `key = service name`.
- **programs**: Uninstall subkeys (HKLM + Wow6432 + HKCU); require `DisplayName`; `detail = {version,
  publisher, install_date}`. `key = "<hive>\\<subkey>"`.
- **tasks**: walk `C:\\Windows\\System32\\Tasks`; the relative path is the task name; parse XML
  (namespace-aware) for `Actions/Exec/Command`+`Arguments`, `Settings/Enabled`, trigger count.
  `key = task path`.

Each collector is independently `try`-wrapped so one failing surface degrades gracefully (recorded in
`collectors_ok`), never aborting the snapshot.

## 6. Safety
Read-only against the system (only writes its own SQLite DB). No registry/system write, no delete.
`ref`/ids validated; `filter` is a plain substring. Registry reads use `KEY_WOW64_64KEY`. Runs elevated
but the attack surface is a loopback tool that only reads config + writes an isolated DB.

## 7. Files
`drift_mcp_server.py` (FastMCP, 6 tools) · `collectors.py` · `drift_store.py` · `start_drift_mcp.ps1` /
`install_task.ps1` / `uninstall_task.ps1` (Scheduled Task `Drift-MCP`, 8781; the task can also carry a
daily `snapshot_now` trigger later) · `requirements.txt` · `README.md` · `tests/` · `data/` (gitignored).

## 8. goose extension
```yaml
  drift:
    type: streamable_http
    bundled: false
    name: drift
    enabled: true
    uri: http://127.0.0.1:8781/mcp
    headers: {}
    env_keys: []
    timeout: 120
    description: Windows config-drift (autoruns/services/programs/tasks snapshots + diff) via local elevated MCP server (127.0.0.1:8781)
```

## 9. Out of scope (YAGNI)
Driver-store/pnputil detail, WMI event subscriptions, installed-updates history (own MCP later), auto
daily snapshot scheduling (can be added to the task), remediation/rollback (read-only by design).

## 10. Open risks
- Uninstall/Services enumeration is large (300+ each) — snapshot writes are batched in one transaction.
- Task XML parsing varies; parse defensively per file, skip unreadable ones, count them.
- DB grows with snapshots — `list_snapshots` exposes count; pruning is a manual `delete` (out of scope v1).
