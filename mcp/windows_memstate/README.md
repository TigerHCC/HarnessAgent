# Windows Memory-State MCP

A local, **read-only** MCP for **kernel memory attribution** -- poolmon/RamMap in a tool. Answers
*"where did my RAM go / what is leaking kernel memory"*, which nothing else in the suite can: perfmon
shows nonpaged pool is *growing*, this says *which tag/driver*. There is **no shell equivalent** (poolmon
ships only with the WDK as an interactive console UI; no PowerShell cmdlet exposes pool tags).

Tenth diagnostic MCP (tier-2). Rationale:
[`docs/windows-diagnostic-mcp-candidates.md`](../../docs/windows-diagnostic-mcp-candidates.md) (`memstate`).

## Tools (7)
| Tool | What it answers |
|---|---|
| `pool_tags(sort_by="nonpaged", top_n=30, filter=None)` | Per-tag pool usage (the leak hunt). |
| `memory_composition()` | Physical RAM composition: standby / modified / free / zeroed (GB). |
| `memory_overview()` | Physical/commit totals, kernel pool, system-wide handle/proc/thread counts. |
| `tag_driver(tag)` | Best-effort owning driver(s) for a pool tag (known map + drivers\*.sys scan). |
| `baseline_save(name)` / `baseline_diff(name)` | Which pool tags GREW most since a baseline (leak trend). |
| `memstate_health()` | Admin, ntdll query OK, tag count, physical GB. |

Every tool returns a structured `{...}` (errors as `{"error": ...}`), never raises.

## Run it
```powershell
.\start_memstate_mcp.ps1                        # elevated
.\install_task.ps1 ; Start-ScheduledTask -TaskName mcp-memstate
```
Serves `http://127.0.0.1:8786/mcp`. Pure stdlib -- `ctypes` against ntdll `NtQuerySystemInformation`
(SystemPoolTagInformation 0x16 / SystemMemoryListInformation 0x50) + psapi `GetPerformanceInfo`.
Baselines in `data/` (gitignored; override with `MEMSTATE_BASELINES`).

## goose extension config
```yaml
  memstate:
    type: streamable_http
    bundled: false
    name: memstate
    enabled: true
    uri: http://127.0.0.1:8786/mcp
    headers: {}
    env_keys: []
    timeout: 120
    description: Windows memory attribution (pool tags / physical-memory composition / kernel-pool leak hunt) via local elevated MCP server (127.0.0.1:8786)
```

## Typical use
```
pool_tags(top_n=15)                 # who's using nonpaged pool now
baseline_save(name="clean")         # ... time passes / suspected leak grows ...
baseline_diff()                     # which tag grew -> tag_driver("Xyz ") -> the leaking driver
```

## Notes
- **A single snapshot can't tell a leak from steady-state usage** -- the *trend* (`baseline_diff`) is the
  signal. High + growing nonpaged for one tag = a leaking driver.
- `tag_driver` is heuristic (byte-scans drivers\*.sys for the 4-char tag; a tag can match several drivers).
- The memory-list composition may need elevation (SeProfileSingleProcessPrivilege); pool tags work either way.
- Undocumented NtQuerySystemInformation structs are pinned to x64 layouts (SYSTEM_POOLTAG = 40 B) and
  sanity-checked; an implausible parse returns an error, not wrong numbers. Read-only.

## Files
`memstate_mcp_server.py` (FastMCP, 7 tools) · `native.py` (NtQuerySystemInformation + GetPerformanceInfo)
· `pooltags.py` (known-tag map + tag->driver scan) · `start_memstate_mcp.ps1` / `install_task.ps1` /
`uninstall_task.ps1` · `tests/` · `data/`.

Batch-test this server with the [central MCP suite instructions](../README.md#test-all-local-mcp-servers).
