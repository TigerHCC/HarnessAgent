# Windows Netconn MCP

A local, **read-only** MCP server — the **network analogue of SRUM**. SRUM says how much an app
sent/received historically; this says *which process/service owns every socket RIGHT NOW*, what's
listening, whether the connection table is under pressure, and — via saved baselines — what
listeners/endpoints are NEW (rogue listener / beaconing). Answers what `netstat` can't: **svchost PID →
the actual hosted service**, per-process TIME_WAIT/CLOSE_WAIT pressure, and diff vs a known-good baseline.

Sixth sibling of srum(8777)/eventlog(8778)/crash(8779)/exec(8780)/drift(8781). Rationale:
[`docs/windows-diagnostic-mcp-candidates.md`](../../docs/windows-diagnostic-mcp-candidates.md)
(candidate #5).

## Tools (7)
| Tool | What it answers |
|---|---|
| `connections(state, proto, pid, port, process, max=200)` | Current sockets + owning process/service, filtered. |
| `listeners(max=200)` | What is listening (TCP LISTEN + bound UDP) with owner. |
| `connection_stats()` | Counts by state/proto, top processes (TIME_WAIT/CLOSE_WAIT), ephemeral-port usage. |
| `by_remote(ip=None, max=200)` | Outbound/established grouped by remote endpoint + owner. |
| `baseline_save(name="default")` | Snapshot current listeners + remote endpoints as a baseline. |
| `baseline_diff(name="default")` | New/removed listeners+remotes vs the baseline (rogue/beacon detection). |
| `netconn_health()` | Admin status, psutil OK, socket count, service-map OK, baseline names. |

Every tool returns a structured `{...}` (errors as `{"error": ...}`), never raises. Caps observable.

## Run it
```powershell
.\start_netconn_mcp.ps1                        # elevated
# or persist as a logon Scheduled Task 'mcp-netconn':
.\install_task.ps1 ; Start-ScheduledTask -TaskName mcp-netconn
```
Serves `http://127.0.0.1:8782/mcp`. Uses **psutil** for the socket table + process names and
`tasklist /svc` for svchost→service resolution (cached ~15 s). Baselines live in `data/`
(gitignored; override with `NETCONN_BASELINES`).

## goose extension config
```yaml
  netconn:
    type: streamable_http
    bundled: false
    name: netconn
    enabled: true
    uri: http://127.0.0.1:8782/mcp
    headers: {}
    env_keys: []
    timeout: 120
    description: Windows live network connections + owning process/service + baseline diff via local elevated MCP server (127.0.0.1:8782)
```

## Notes
- **Read-only**: no socket is opened/closed/killed; the ONLY thing written is the JSON baseline
  (atomic replace under a lock).
- Owning PIDs of protected processes need admin; not elevated → some rows report `pid=null`/`exe=null`.
- UDP has no LISTEN state; a bound UDP socket with no remote is surfaced as `state=LISTEN` so it appears
  in `listeners()`.
- `services` is every service hosted in that PID (tasklist granularity), not the single service owning
  the specific socket.

## Files
`netconn_mcp_server.py` (FastMCP, 7 tools) · `netconn_reader.py` (psutil table + pid→exe + svchost→service
+ JSON baselines) · `start_netconn_mcp.ps1` / `install_task.ps1` / `uninstall_task.ps1` · `tests/` · `data/`.

Batch-test this server with the [central MCP suite instructions](../README.md#test-all-local-mcp-servers).
