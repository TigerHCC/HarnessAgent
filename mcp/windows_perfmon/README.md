# Windows Perfmon MCP

A local MCP server for **real-time system performance counters via PDH** — the live complement to SRUM.
SRUM (and psutil) give historical/coarse usage; this gives the metrics that actually answer *"what is
the bottleneck RIGHT NOW"* and that psutil can't: **disk latency** (Avg Disk sec/Transfer), **pool
nonpaged/paged** (kernel-leak detection), **hard-paging** (Pages/sec), and **% Processor Utility**
(Task-Manager-accurate). Locale-safe (uses `PdhAddEnglishCounter`, so it works on non-English Windows).

Seventh sibling of srum(8777)/eventlog(8778)/crash(8779)/exec(8780)/drift(8781)/netconn(8782).
Rationale: [`docs/windows-diagnostic-mcp-candidates.md`](../../docs/windows-diagnostic-mcp-candidates.md)
(candidate #3).

## Tools (6)
| Tool | What it answers |
|---|---|
| `snapshot(delay_ms=1000)` | Current CPU/disk/memory counters (grouped). |
| `bottleneck(delay_ms=1000)` | Thresholded verdict: CPU-saturated / disk-latency / low-mem / paging? |
| `counters(paths, delay_ms=1000)` | Read arbitrary **single-instance** PDH counter paths. Returns `{"values": {...}}`. |
| `baseline_save(name="default")` | Persist current counters as a named baseline. |
| `baseline_diff(name="default")` | Numeric delta of every counter vs a baseline ("has nonpaged pool grown?"). |
| `perfmon_health()` | Admin status, PDH availability, a quick sample. |

Every tool returns a structured `{...}` (errors as `{"error": ...}`), never raises.

## Run it
```powershell
.\start_perfmon_mcp.ps1                        # elevated recommended (a few counters need admin)
.\install_task.ps1 ; Start-ScheduledTask -TaskName mcp-perfmon
```
Serves `http://127.0.0.1:8783/mcp`. Pure stdlib — `ctypes` against `pdh.dll` (no pywin32). Baselines in
`data/` (gitignored; override with `PERFMON_BASELINES`).

## goose extension config
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

## Notes
- **Rate counters need two samples**: `snapshot`/`bottleneck` collect, wait `delay_ms`, collect again —
  so a call takes ~`delay_ms`. Instantaneous counters (Available MB, pool bytes, queue lengths) are exact.
- **English counter paths** are used internally (`PdhAddEnglishCounter`) so the tool is locale-independent;
  a custom `counters([...])` call must also use English counter names.
- Only **single-instance** counters are supported (via `PdhGetFormattedCounterValue`). Wildcard /
  multi-instance paths (e.g. `\GPU Engine(*)\Utilization Percentage`) need `PdhGetFormattedCounterArray`
  and are **not** implemented in v1 — pass an explicit instance name instead.
- Read-only: only ever *reads* performance data; the only write is its own JSON baseline.

## Files
`perfmon_mcp_server.py` (FastMCP, 6 tools) · `pdh_reader.py` (ctypes PDH + curated counter set +
bottleneck heuristic + JSON baselines) · `start_perfmon_mcp.ps1` / `install_task.ps1` /
`uninstall_task.ps1` · `tests/` · `data/`.

Batch-test this server with the [central MCP suite instructions](../README.md#test-all-local-mcp-servers).
