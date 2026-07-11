# Windows Process-Inspection MCP

A local, **read-only** Process Explorer-style MCP: the answers the shell and psutil alone can't give.

- **Who locks this file** -- the #1 "can't delete / can't update / file in use" question (Restart Manager API).
- **Why is a process hung** -- Wait Chain Traversal (Thread -> lock -> owning thread) with **deadlock** detection.
- **Loaded modules** (+ Authenticode) -- spot an injected / unsigned DLL in a misbehaving app.
- **Handle-leak candidates** -- processes ranked by open-handle count (a live probe here immediately
  surfaced a ~1.96M-handle leak in a cert-signing helper).
- **Deep process detail** -- exe/cmdline/parent/user/handles/threads/memory/cpu/files/connections.

Ninth sibling (first tier-2) of srum..disk. Rationale:
[`docs/windows-diagnostic-mcp-candidates.md`](../../docs/windows-diagnostic-mcp-candidates.md) (`procinspect`).

## Tools (7)
| Tool | What it answers |
|---|---|
| `who_locks(path)` | Which processes have this file/dir open (Restart Manager). |
| `wait_chain(pid=None, tid=None)` | Why a process/thread is hung: wait chain + deadlock flag. |
| `process_detail(pid)` | Deep per-process detail. |
| `loaded_modules(pid, filter, check_signatures=False, max=300)` | Loaded DLLs, optionally Authenticode-checked. |
| `top_handle_users(n=15)` | Processes ranked by open-handle count (leak view). |
| `find_process(name, max=50)` | Find processes by name substring. |
| `procinspect_health()` | Admin status, psutil, process count. |

Every tool returns a structured `{...}` (errors as `{"error": ...}`), never raises.

## Run it
```powershell
.\start_procinspect_mcp.ps1                         # elevated recommended
.\install_task.ps1 ; Start-ScheduledTask -TaskName Procinspect-MCP
```
Serves `http://127.0.0.1:8785/mcp`. `native.py` uses `ctypes` against rstrtmgr.dll (Restart Manager) +
advapi32.dll (Wait Chain Traversal); `procdetail.py` uses psutil (+ one `Get-AuthenticodeSignature` call
when `check_signatures=True`).

## goose extension config
```yaml
  procinspect:
    type: streamable_http
    bundled: false
    name: procinspect
    enabled: true
    uri: http://127.0.0.1:8785/mcp
    headers: {}
    env_keys: []
    timeout: 120
    description: Windows process inspection (who-locks-a-file, hang/deadlock wait chains, loaded modules, handle-leak view) via local MCP server (127.0.0.1:8785)
```

## Notes
- **Strictly read-only**: no process is killed / suspended / debugged / modified.
- Elevated recommended for cross-process handle/module detail; protected processes -> AccessDenied (handled).
- Wait Chain Traversal marks idle worker threads "Blocked"; only multi-node / deadlock chains are surfaced
  as `blocked_chains`, with lone-blocked threads counted separately (avoids false "hang" noise).
- `check_signatures` is opt-in (spawns one PowerShell call over the module paths); capped by `max`.
- Deep handle-table enumeration + per-handle object names (NtQueryObject) is v2 (hang-prone, deferred).

## Files
`procinspect_mcp_server.py` (FastMCP, 7 tools) · `native.py` (Restart Manager + Wait Chain Traversal) ·
`procdetail.py` (psutil detail/modules/handle-ranking) · `start_procinspect_mcp.ps1` / `install_task.ps1`
/ `uninstall_task.ps1` · `tests/`.
