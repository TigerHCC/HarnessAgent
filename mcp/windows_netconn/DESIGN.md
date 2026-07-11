# Windows Netconn MCP — Design Spec

> Date: 2026-07-11 · Status: approved → implementation
> Location: `HarnessAgent/mcp/windows_netconn/` · Boundary: **never modifies `PersonalKnowledge-GB10`**
> Sixth sibling of srum/eventlog/crash/exec/drift. Rationale + ranking:
> `docs/windows-diagnostic-mcp-candidates.md` (candidate #5, value 5).

## 1. Goal
The **network analogue of SRUM**: SRUM says how much an app sent/received historically; this says
*which process/service owns every socket RIGHT NOW*, what's listening, whether the connection table is
under pressure (port exhaustion), and — via saved baselines — what network endpoints/listeners are NEW
(rogue listener / beaconing detection). Answers the questions `netstat` can't: svchost PID → the actual
hosted service; per-process TIME_WAIT/CLOSE_WAIT pressure; diff vs a known-good baseline.

## 2. Architecture
```
  goose (USER mode) ──streamable_http──▶ 127.0.0.1:8782/mcp
   └ extension: netconn                      │
                                             ▼
                    netconn_mcp_server.py  (ELEVATED / admin, READ-ONLY vs the system)
                      └ netconn_reader.py  (psutil connection table + pid->exe + svchost pid->service
                                            via tasklist /svc; JSON baselines in data/)
```
Runs **elevated** so owning PIDs of protected processes resolve. Bind `127.0.0.1:8782`, streamable HTTP
(FastMCP). Uses **psutil** (already installed, 7.0.0) for the socket table + process names; `tasklist
/svc` for svchost service resolution; stdlib for everything else. Read-only vs the system — the only
thing written is its own JSON baseline file (`data/`, gitignored).

## 3. Ground truth (verified on this box)
- `psutil.net_connections(kind="inet")` → 288 sockets: TCP 208 / UDP 80; states ESTABLISHED / LISTEN /
  TIME_WAIT / CLOSE_WAIT / FIN_WAIT2 / NONE(UDP). Each: `type` (SOCK_STREAM/DGRAM), `laddr`(ip,port),
  `raddr`(() if none), `status`, `pid` (None for some without privilege).
- `tasklist /svc /fo csv` → columns `Image Name, PID, Services`; svchost rows list comma-separated
  hosted services (e.g. pid 2008 → `RpcEptMapper,RpcSs`). This is the svchost→service resolution
  netstat/Get-NetTCPConnection can't give.
- Windows default dynamic (ephemeral) port range is 49152–65535 → used for port-exhaustion diagnosis.

## 4. Tool surface (7)
- `connections(state=None, proto=None, pid=None, port=None, process=None, max=200)` → filtered current
  sockets `[{proto, local, lport, remote, rport, state, pid, exe, services}]`.
- `listeners(max=200)` → TCP LISTEN + bound-UDP sockets with owner — "what is listening on this box".
- `connection_stats()` → `{total, by_state, by_proto, top_processes:[{exe, pid, count, time_wait,
  close_wait}], ephemeral:{range, in_use, pct}}` — port-exhaustion / leak diagnosis.
- `by_remote(ip=None, max=200)` → outbound/established grouped by remote endpoint + owner — "who is
  talking to X / what is process Y connected to".
- `baseline_save(name="default")` → snapshot the current listener + remote-endpoint signatures to
  `data/netconn_baselines.json`.
- `baseline_diff(name="default")` → `{added:[], removed:[]}` of listeners/remotes vs the saved baseline
  — new listener (rogue) / new remote endpoint (beaconing) detection.
- `netconn_health()` → `{is_admin, psutil_ok, socket_count, service_map_ok, baselines:[names]}`.

Every tool returns a structured `{error:…}`, never raises. Caps observable (`truncated`/`total`).

## 5. Safety
Read-only vs the system (only writes its own JSON baseline, atomic os.replace under a lock). No socket is
opened/closed/killed. `process`/`ip` filters are plain substrings. Protected-process PIDs whose name is
unreadable return `exe=None` gracefully (never crash). Runs elevated but is a loopback read-only tool.

## 6. Files
`netconn_mcp_server.py` (FastMCP, 7 tools) · `netconn_reader.py` · `start_netconn_mcp.ps1` /
`install_task.ps1` / `uninstall_task.ps1` (Scheduled Task `Netconn-MCP`, 8782) · `requirements.txt`
(mcp, psutil, pytest) · `README.md` · `tests/` · `data/` (gitignored).

## 7. goose extension
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

## 8. Out of scope (YAGNI)
Per-connection TCP ESTATS (retransmits). Exact svchost socket→single-service (tasklist gives all
services in the PID, not the one owning the socket). Packet capture. Cross-machine. Any socket mutation.

## 9. Open risks
- `tasklist /svc` spawn cost per call → cache ~15 s. psutil.net_connections is a single fast syscall.
- pid=None sockets (reserved/TIME_WAIT or insufficient privilege) → reported with pid=null, exe=null.
- Baseline JSON concurrent writes from the threaded server → guarded by a lock + atomic replace.
