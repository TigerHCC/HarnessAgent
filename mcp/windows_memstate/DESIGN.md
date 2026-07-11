# Windows Memory-State MCP — Design Spec

> Date: 2026-07-11 · Status: approved → implementation
> Location: `HarnessAgent/mcp/windows_memstate/` · Boundary: never modifies `PersonalKnowledge-GB10`
> Tenth diagnostic MCP (tier-2). Rationale: `docs/windows-diagnostic-mcp-candidates.md` (`memstate`, value 4).
> poolmon/RamMap-style memory attribution — nothing else in the suite (perfmon shows pool is *growing*;
> this says *which tag/driver*).

## 1. Goal
Answer **"where did my RAM go / what is leaking kernel memory"**:
- **Pool tags** (poolmon) — per-tag paged/nonpaged pool bytes + alloc/free counts. The nonpaged-pool
  leak hunt: which tag is consuming/leaking, and (best-effort) which driver owns it.
- **Physical memory composition** (RamMap) — standby / modified / free / zeroed page lists.
- **Overview** — physical/commit/kernel-pool totals + system-wide handle/process/thread counts.
- **Trend** — save a pool-tag baseline and diff, so "which tag grew since last week" is answerable
  (a single snapshot can't distinguish a leak from steady-state usage — the trend is the signal).

There is **no shell equivalent**: poolmon ships only in the WDK and is an interactive console UI; there
is no PowerShell cmdlet for pool tags or the memory lists.

## 2. Architecture
```
  goose (USER) ──streamable_http──▶ 127.0.0.1:8786/mcp
   └ extension: memstate                │
                                        ▼
                memstate_mcp_server.py  (ELEVATED, READ-ONLY)
                  ├ native.py    (ctypes NtQuerySystemInformation: SystemPoolTagInformation 0x16,
                  │               SystemMemoryListInformation 0x50; psapi GetPerformanceInfo)
                  └ pooltags.py  (curated known-tag map + on-demand tag->driver scan of drivers\*.sys)
```
Runs **elevated** (SystemMemoryListInformation needs SeProfileSingleProcessPrivilege; pool-tag
enumeration works unelevated but the server runs elevated by design). Bind `127.0.0.1:8786`, streamable
HTTP (FastMCP). Read-only. Pure stdlib + a small JSON baseline (`data/`, gitignored).

## 3. Ground truth (verified this box, x64)
- `NtQuerySystemInformation` (ntdll): STATUS_INFO_LENGTH_MISMATCH (0xC0000004) → resize loop.
- `SystemPoolTagInformation`=0x16 → ULONG Count + Count × SYSTEM_POOLTAG. **SYSTEM_POOLTAG = 40 bytes**
  {char Tag[4]; ULONG PagedAllocs, PagedFrees; SIZE_T PagedUsed; ULONG NonPagedAllocs, NonPagedFrees;
  SIZE_T NonPagedUsed} (array starts at offset 8 after Count+pad). Verified: 3760 tags; top nonpaged
  `ismc` 350 MB, `EtwB` 76 MB, etc.
- `SystemMemoryListInformation`=0x50 → 5× ULONG_PTR (Zero/Free/Modified/ModifiedNoWrite/Bad page counts)
  + PageCountByPriority[8] (standby) + RepurposedByPriority[8] + ModifiedPageFile. Pages × 4096 = bytes.
  Verified: standby 9.3 GB.
- `GetPerformanceInfo` (psapi) PERFORMANCE_INFORMATION: Physical/Commit/Kernel* in pages, + Handle/
  Process/ThreadCount. Verified: 23.7 GB phys, 6.2 M system-wide handles.

## 4. Tool surface (7)
- `pool_tags(sort_by="nonpaged", top_n=30, filter=None)` → `{total_nonpaged_mb, total_paged_mb,
  total_tag_count, matched_tag_count, count, tags:[{tag, description?, nonpaged_mb, paged_mb,
  nonpaged_allocs, nonpaged_outstanding}]}`.
- `memory_composition()` → `{physical_total_gb, physical_available_gb, standby_gb, modified_gb, free_gb,
  zeroed_gb, standby_by_priority_gb}`.
- `memory_overview()` → `{physical_total_gb, physical_available_gb, commit_total_gb, commit_limit_gb,
  kernel_paged_mb, kernel_nonpaged_mb, handles, processes, threads}`.
- `tag_driver(tag)` → best-effort owning driver(s): the curated known-tag description + a scan of
  `drivers\*.sys` for the 4-byte tag (cached).
- `baseline_save(name="default")` / `baseline_diff(name="default")` → per-tag pool delta over time
  (the leak signal): tags whose nonpaged/paged use grew most.
- `memstate_health()` → `{is_admin, ntdll_ok, tag_count, physical_gb}`.

Every tool returns a structured `{error:…}`, never raises. Caps observable.

## 5. Safety
Read-only: only queries kernel counters; no memory is written/freed. `tag` validated (≤4 chars). The
driver scan reads `%SystemRoot%\System32\drivers\*.sys` read-only, size-capped, cached. Only write is
the JSON baseline (atomic os.replace under a lock). ctypes structs match the Win32 layouts (POOLTAG 40B,
PERFORMANCE_INFORMATION) and NtQuerySystemInformation uses a length-mismatch resize loop.

## 6. Files
`memstate_mcp_server.py` (FastMCP, 7 tools) · `native.py` · `pooltags.py` · `start_memstate_mcp.ps1` /
`install_task.ps1` / `uninstall_task.ps1` (Scheduled Task `Memstate-MCP`, 8786) · `requirements.txt`
(mcp, pytest) · `README.md` · `tests/` · `data/` (gitignored).

## 7. goose extension
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

## 8. Out of scope (YAGNI)
Per-process working-set attribution (that's perfmon/srum). Driver Verifier. Pool tag -> driver is
heuristic (a tag can be used by multiple drivers). Editing/trimming memory.

## 9. Open risks
- Undocumented NtQuerySystemInformation structs — pinned to the x64 layouts, validated by sizeof + a
  sanity check on Count/values; on an implausible parse return raw + a flag rather than wrong numbers.
- tag→driver is heuristic (byte match); return all matches + a caveat.
- SystemMemoryListInformation may need elevation → structured "requires admin" if it fails.
