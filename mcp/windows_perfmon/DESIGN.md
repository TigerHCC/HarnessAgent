# Windows Perfmon MCP — Design Spec

> Date: 2026-07-11 · Status: approved → implementation
> Location: `HarnessAgent/mcp/windows_perfmon/` · Boundary: never modifies `PersonalKnowledge-GB10`
> Seventh diagnostic MCP (tier-1, candidate #3). Rationale: `docs/windows-diagnostic-mcp-candidates.md`.

## 1. Goal
Real-time system performance via **PDH** counters — the live complement to SRUM. Gives what psutil can't:
- **disk latency** (`Avg. Disk sec/Transfer`) — the "is the disk slow" metric,
- **pool nonpaged/paged bytes** — kernel-leak early warning (pairs with `memstate` for the *which driver*),
- **hard-paging** (`Pages/sec`) and **% Processor Utility** (Task-Manager-accurate CPU),
plus named baselines so "what changed since 10 minutes ago" is answerable. **Locale-safe**: uses
`PdhAddEnglishCounter`, so counter paths work on non-English Windows (localized names otherwise break).

## 2. Architecture
```
  goose (USER) ──streamable_http──▶ 127.0.0.1:8783/mcp
   └ extension: perfmon                 │
                                        ▼
                perfmon_mcp_server.py  (elevated recommended, READ-ONLY)
                  └ pdh_reader.py  (ctypes pdh.dll: PdhOpenQuery / PdhAddEnglishCounterW /
                                    PdhCollectQueryData x2 / PdhGetFormattedCounterValue; JSON baselines)
```
Bind `127.0.0.1:8783`, streamable HTTP (FastMCP). Read-only — only reads performance counters. Pure
stdlib (`ctypes` against `pdh.dll`; no pywin32). Rate counters need TWO `PdhCollectQueryData` samples, so
`snapshot`/`bottleneck` collect → sleep `delay_ms` → collect again.

## 3. Ground truth (verified this box, x64)
- `PdhOpenQueryW` + `PdhAddEnglishCounterW` + `PdhCollectQueryData` + `PdhGetFormattedCounterValue`
  (PDH_FMT_DOUBLE). `PDH_FMT_COUNTERVALUE` double read at struct offset 8 (DWORD CStatus + 4 pad + double
  on x64). Verified live: CPU % Utility, Avg Disk sec/Transfer, Available MBytes, Pool Nonpaged Bytes,
  Pages/sec all read correctly. HANDLEs sized via byref (no truncation).
- Curated single-instance counters (`_Total` instances avoid wildcard expansion): CPU (% Processor
  Utility / % Privileged / queue length / context switches), disk (Avg sec/Read|Write|Transfer / queue /
  % idle), memory (Available MB / % committed / pool nonpaged|paged / Pages|Page-Faults per sec).

## 4. Tool surface (6)
- `snapshot(delay_ms=1000)` → `{sampled_ms, counters:{cpu, disk, memory, system}}`.
- `bottleneck(delay_ms=1000)` → `{verdict, findings:[{metric, value, threshold, note}], checked}` —
  thresholded "is the bottleneck now CPU / disk latency / low-mem / paging".
- `counters(paths, delay_ms=1000)` → `{values:{path: value|null}, counter_errors?}` for arbitrary
  SINGLE-INSTANCE English counter paths (wildcards need PdhGetFormattedCounterArray — not v1).
- `baseline_save(name="default")` / `baseline_diff(name="default")` → numeric delta of every counter vs a
  baseline ("has nonpaged pool grown since?").
- `perfmon_health()` → `{is_admin, pdh_ok, sample, counter_count}`.

Every tool returns a structured `{error:…}`, never raises.

## 5. Safety
Read-only: only reads PDH counters; no system state changed. PDH query handle + per-counter handles
opened and `PdhCloseQuery`d in `finally`. The only write is the JSON baseline (atomic os.replace under a
lock). `delay_ms`/counter paths validated; a huge `delay_ms` (OverflowError) returns a structured error.

## 6. Files
`perfmon_mcp_server.py` (FastMCP, 6 tools) · `pdh_reader.py` (ctypes PDH + curated counter set +
bottleneck heuristic + JSON baselines) · `start_perfmon_mcp.ps1` / `install_task.ps1` /
`uninstall_task.ps1` (Scheduled Task `Perfmon-MCP`, 8783) · `requirements.txt` (mcp, pytest) · `README.md`
· `tests/` · `data/` (gitignored).

## 7. goose extension
```yaml
  perfmon:
    type: streamable_http
    bundled: false
    name: perfmon
    enabled: true
    uri: http://127.0.0.1:8783/mcp
    headers: {}
    env_keys: []
    timeout: 120
    description: Windows real-time performance counters (CPU/disk-latency/memory/pool via PDH) + baselines via local MCP server (127.0.0.1:8783)
```

## 8. Out of scope (YAGNI)
Wildcard / multi-instance counters (need PdhGetFormattedCounterArray — v2). GPU/thermal (firmware/wildcard
dependent — pass explicit single-instance paths via `counters()`). Per-process top-N (that's srum/psutil).

## 9. Open risks
- Rate counters need two samples → a call takes ~`delay_ms`; a failed 2nd collect is recorded in
  `counter_errors._second_sample` (rate counters may be null) rather than nuking non-rate counters.
- Undocumented `% Processor Utility` matches Task Manager (unlike `% Processor Time` on modern CPUs).
- Baseline JSON tolerates a non-dict / corrupt file (coerced to `{}`, entry validated) — never raises.
