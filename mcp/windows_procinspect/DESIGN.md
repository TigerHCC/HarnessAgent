# Windows Process-Inspection MCP — Design Spec

> Date: 2026-07-11 · Status: approved → implementation
> Location: `HarnessAgent/mcp/windows_procinspect/` · Boundary: never modifies `PersonalKnowledge-GB10`
> Ninth sibling (first tier-2). Rationale: `docs/windows-diagnostic-mcp-candidates.md` (candidate
> `procinspect`, value 5). Process Explorer-style answers the shell/psutil alone can't give.

## 1. Goal
- **Who locks this file** (the #1 real-world "can't delete / can't update" question) — Restart Manager API.
- **Why is a process hung** — Wait Chain Traversal (Thread → lock → owning thread), with deadlock detection.
- **Loaded modules** (+ Authenticode) — spot an injected / unsigned DLL in a misbehaving app.
- **Handle-leak candidates** — processes ranked by open-handle count.
- **Deep process detail** — exe/cmdline/parent/user/handles/threads/memory/cpu/files/conns.

## 2. Architecture
```
  goose (USER) ──streamable_http──▶ 127.0.0.1:8785/mcp
   └ extension: procinspect              │
                                         ▼
                procinspect_mcp_server.py  (elevated recommended, READ-ONLY)
                  ├ native.py     (ctypes: Restart Manager RmGetList; Wait Chain Traversal GetThreadWaitChain)
                  └ procdetail.py (psutil deep detail + loaded modules + handle-leak ranking + Authenticode)
```
Bind `127.0.0.1:8785`, streamable HTTP (FastMCP). Read-only — only *queries* (no process is killed,
suspended, or modified). Elevated recommended for cross-process handle/module detail; degrades gracefully
(AccessDenied → structured error) when not.

## 3. Ground truth (verified this box)
- Restart Manager: `RmStartSession`/`RmRegisterResources`/`RmGetList`/`RmEndSession` (rstrtmgr.dll);
  `RM_PROCESS_INFO` = 668 bytes; RmGetList returns holding pid + app name for a locked file.
- Wait Chain Traversal: `OpenThreadWaitChainSession`/`GetThreadWaitChain`/`Close…` (advapi32.dll);
  `WAITCHAIN_NODE_INFO` = 280 bytes; returns Thread/lock nodes + an IsCycle deadlock flag.
- psutil: num_handles / num_threads / memory_maps / open_files / net_connections all work. (Live probe
  immediately surfaced a real ~1.96M-handle leak in CGServiSign*.exe.)

## 4. Tool surface (7)
- `who_locks(path)` → `{path, count, holders:[{pid, app, service, type, restartable}]}`.
- `wait_chain(pid=None, tid=None)` → `{thread_count, blocked_chains:[{tid, is_deadlock, nodes:[{type,
  status, pid?, tid?, wait_ms?, name?}]}], deadlock_detected, idle_blocked_threads}`.
- `process_detail(pid)` → exe/cmdline/parent/user/create_time/status/num_handles/num_threads/memory/
  cpu/open_files/connections/ctx_switches.
- `loaded_modules(pid, filter=None, check_signatures=False, max=300)` → `{modules:[{path, name,
  signature?}], unsigned_or_untrusted?}`.
- `top_handle_users(n=15)` → processes ranked by open-handle count (leak view).
- `find_process(name="", max=50)` → matching processes with pid/handles/threads/user.
- `procinspect_health()` → `{is_admin, psutil_ok, process_count}`.

Every tool returns a structured `{error:…}`, never raises. Caps observable.

## 5. Safety
Strictly read-only: no process is killed / suspended / debugged / modified. `who_locks` path is an API
arg (no shell). `check_signatures` batches one `Get-AuthenticodeSignature` call over the module paths
(quoted). Wait Chain session + RM session always closed in `finally`. ctypes structs match the Win32
layouts (RM_PROCESS_INFO 668B, WAITCHAIN_NODE_INFO 280B) and HANDLEs are correctly sized.

## 6. Files
`procinspect_mcp_server.py` (FastMCP, 7 tools) · `native.py` (RM + WCT) · `procdetail.py` (psutil) ·
`start_procinspect_mcp.ps1` / `install_task.ps1` / `uninstall_task.ps1` (Scheduled Task `Procinspect-MCP`,
8785) · `requirements.txt` (mcp, psutil, pywin32?, pytest) · `README.md` · `tests/`.

## 7. goose extension
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

## 8. Out of scope (YAGNI)
Full handle-table enumeration + per-handle names via NtQuerySystemInformation/NtQueryObject (the
hang-prone deep path — v2). GDI/USER object breakdown. Minidump of a hung process (that's the crash MCP).
Killing/suspending processes (never — read-only).

## 9. Open risks
- Wait Chain Traversal marks idle worker threads "Blocked"; we only surface multi-node/deadlock chains as
  `blocked_chains` and count lone-blocked threads separately to avoid false "hang" noise.
- Cross-process num_handles/memory_maps need elevation for some protected processes → AccessDenied handled.
- `check_signatures` spawns a PowerShell call → opt-in only (slower); capped by `max`.
