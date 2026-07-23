# Windows Config-Drift MCP

A local, **read-only** (vs the system) MCP server that answers the killer diagnostic question
**"what changed on this machine right before the problem started?"** The shell only sees *now*; this
persists point-in-time snapshots to SQLite and diffs them over time.

Tracks: **autoruns** (Run/RunOnce, Winlogon, Startup folders) · **services**/drivers · **programs**
(Uninstall keys) · **scheduled tasks**. Fifth sibling of srum(8777)/eventlog(8778)/crash(8779)/exec(8780).
Rationale: [`docs/windows-diagnostic-mcp-candidates.md`](../../docs/windows-diagnostic-mcp-candidates.md)
(candidate #4). **Build a baseline early** — value accrues over time; you need a "good" snapshot before
the next incident.

## Tools (6)
| Tool | What it does |
|---|---|
| `snapshot_now(note=None)` | Capture + persist a snapshot of all four categories. |
| `list_snapshots()` | Saved snapshots (id, ts, note, item count), newest first. |
| `current(category=None, filter=None, max=200)` | Live enumeration of the current config (no persist). |
| `diff(a=None, b=None, category=None)` | Added/removed/changed between snapshot `a` (default latest) and `b` (default **live now**). |
| `what_changed_since(ref)` | `ref` = snapshot id or ISO date → diff that point vs now. The headline. |
| `drift_health()` | Admin status, DB path, snapshot count, live collector counts. |

Every tool returns a structured `{...}` (errors as `{"error": ...}`), never raises. Diff lists are capped
at 500 with a `truncated` flag + per-category counts.

## Run it
```powershell
.\start_drift_mcp.ps1                        # elevated
# or persist as a logon Scheduled Task 'mcp-drift':
.\install_task.ps1 ; Start-ScheduledTask -TaskName mcp-drift
```
Serves `http://127.0.0.1:8781/mcp`. Pure stdlib (`winreg`, `sqlite3`, `xml.etree`). The DB lives at
`data/drift.db` (gitignored; override with `DRIFT_DB`).

## goose extension config
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

## Typical use
```
snapshot_now(note="clean baseline")     # today, while healthy
... time passes, something breaks ...
what_changed_since("2026-07-11")        # what services/autoruns/programs/tasks appeared or changed
```

## Notes
- **Read-only vs the system**: the ONLY thing written is `data/drift.db`. No registry/system write.
- Services + scheduled tasks need admin; not elevated → those collectors report an error but the snapshot
  still captures what it can (`collectors_ok` / `collector_errors`).
- `diff` with `b` omitted compares a saved snapshot against the live current state (no second snapshot
  needed).

## Files
`drift_mcp_server.py` (FastMCP, 6 tools) · `collectors.py` (4 collectors) · `drift_store.py` (SQLite
snapshot/diff) · `start_drift_mcp.ps1` / `install_task.ps1` / `uninstall_task.ps1` · `tests/` · `data/`.

Batch-test this server with the [central MCP suite instructions](../README.md#test-all-local-mcp-servers).
