# Windows SRUM MCP — Design Spec

> Date: 2026-06-29 · Status: approved (brainstorm) → ready for implementation plan
> Location: `HarnessAgent/mcp/windows_srum/` · Boundary: **never modifies `PersonalKnowledge-GB10`**

## 1. Goal
Give the Windows harness agent (goose) tools to inspect this machine's resource usage:
- **Live / current**: CPU, memory, network throughput, battery power — via `psutil` + WMI.
- **Historical / per-app**: CPU, network bytes, and energy/power consumption over hours/days —
  via Windows **SRUM** (`SRUDB.dat`).

SRUM is historical, per-app, flushed ~hourly — not real-time; live tools cover "right now".

## 2. Architecture
```
Windows machine (single host)
  goose (USER mode)  ──streamable_http──▶  127.0.0.1:8777/mcp
   └ extension: srum                          │
                                              ▼
                         srum_mcp_server.py  (ELEVATED / admin)
                           ├ live_metrics.py  (psutil + WMI)
                           └ srum_reader.py   (esentutl copy → dissect.esedb parse)
```
- The MCP server runs **elevated** (admin) so it can read the locked, admin-only `SRUDB.dat`.
- goose runs in **user mode** and talks to it over **loopback HTTP** — privilege is decoupled
  (same pattern as the DTM mcp-proxy, but local). This is why a networked transport is used
  instead of stdio (a stdio child would inherit goose's non-elevated token).
- Bind **`127.0.0.1:8777`** only (SRUM is per-machine data; loopback avoids exposing it on LAN).
- Transport: **streamable HTTP** via the `mcp` Python SDK (FastMCP). goose extension uses
  `type: streamable_http`, `uri: http://127.0.0.1:8777/mcp`.
  **⚠ Goose 1.39 dropped SSE** — the goose side MUST use `streamable_http`/`/mcp`. (If the SDK
  also exposes `/sse`, that's only for non-goose clients.)

## 3. Constraints / assumptions
- Windows only. Python 3.13 already present; `psutil` + `mcp` already installed; `dissect.esedb`
  to be added. Server process must be elevated for SRUM (live tools would work unelevated, but
  the single server runs elevated by design).
- All code under `HarnessAgent/mcp/windows_srum/`. No changes to `PersonalKnowledge-GB10`.

## 4. Tool surface
### Live (instant)
- `live_snapshot()` → object:
  - `cpu`: { percent_total, percent_per_core[], load_lastmin? }
  - `memory`: { total, available, used, percent, swap_total, swap_used }
  - `disk_io`: { read_bytes_per_s, write_bytes_per_s } (sampled over ~0.5s)
  - `network`: { per_nic: [{name, sent_bytes_per_s, recv_bytes_per_s}], total_sent_per_s, total_recv_per_s }
  - `power`: { battery_percent, plugged_in, secs_left, discharge_rate_mw? } (mW via WMI `BatteryStatus`; null on desktops)
  - `uptime_seconds`, `top_cpu`: [{pid,name,cpu%}], `top_mem`: [{pid,name,rss,mem%}]
- `top_processes(by="cpu"|"memory", n=10)` → list of processes.

### SRUM (historical, admin)
- `srum_app_usage(hours=24, top_n=20)` → per-app: foreground/background CPU cycle time, bytes
  read/written, first/last seen in window. (CPU is **cycle counts**, not seconds — labeled as such.)
- `srum_network_usage(hours=24, top_n=20)` → per-app: bytes sent / received (+ interface if resolvable).
- `srum_energy_usage(hours=24, top_n=20)` → per-app energy/power estimate **and/or** battery
  charge-level history. **Exact columns vary by Windows build → confirmed by a schema-discovery
  spike (Task 1).** Returns what the local schema actually provides, clearly labeled.
- `srum_health()` → { srudb_path, size_mb, last_modified, is_admin, tables_found[], row_counts{},
  parser_ok, cache_age_s }.

All SRUM tools accept `hours` (lookback window) and `top_n`; results sorted desc by the primary metric.

## 5. SRUM parsing approach (`srum_reader.py`)
1. Copy the locked DB: `esentutl.exe /y "%SystemRoot%\System32\sru\SRUDB.dat" /vss /d <temp>\SRUDB.dat`
   (VSS handles the lock; fall back to `/y` without `/vss` if needed). Also run
   `esentutl /p` (repair) on the copy only if it is in a dirty/inconsistent state.
2. Parse with **`dissect.esedb`**: read `SruDbIdMapTable` (IdIndex/IdType/IdBlob → app name or SID),
   then each provider table (GUID-named) joining `AppId` → IdMap, decode timestamps
   (OLE automation / FILETIME per column), filter to the lookback window, aggregate per app.
3. **Cache** the parsed copy for ~10 min (SRUM only flushes hourly) so repeat queries are fast.
   `srum_health` reports cache age; a `force_refresh` is implicit when cache older than TTL.

Known provider table GUIDs (to confirm in spike):
- App Resource Usage `{D10CA2FE-6FCF-4F6D-848E-B2E99266FA89}`
- Network Data Usage `{973F5D5C-1D90-4944-BE8E-24B94231A174}`
- Energy Usage `{FEE4E14F-02A9-4550-B5CE-5FA2DA202E37}` (+ `…}LT` long-term)

## 6. Live metrics approach (`live_metrics.py`)
- `psutil` for cpu/mem/disk/net/processes/battery. Network & disk rates = two samples ~0.5s apart.
- Battery discharge rate (mW): WMI `root\wmi` → `BatteryStatus.DischargeRate` (only meaningful on
  battery; null otherwise). Desktops without a battery → `power` fields null with a note.

## 7. Persistence / startup
- `start_srum_mcp.ps1` — manual launcher; **must be run elevated** (self-checks, prints a clear
  message + re-launch hint if not admin). Starts `python srum_mcp_server.py`.
- `install_task.ps1` — registers a **Scheduled Task** ("Run with highest privileges", trigger:
  at logon) that runs the server elevated automatically. `uninstall_task.ps1` removes it.
- Server is idempotent/safe to restart; single instance (port bind is the lock).

## 8. File structure & responsibilities
| File | Responsibility |
|---|---|
| `srum_mcp_server.py` | FastMCP server; registers tools; wires live_metrics + srum_reader; serves `127.0.0.1:8777/mcp` |
| `live_metrics.py` | pure functions returning live metric dicts (psutil/WMI); no MCP deps → unit-testable |
| `srum_reader.py` | copy + parse SRUDB.dat, caching, aggregation; no MCP deps → unit-testable |
| `start_srum_mcp.ps1` | elevated launcher (admin self-check) |
| `install_task.ps1` / `uninstall_task.ps1` | scheduled-task register/remove |
| `requirements.txt` | `psutil`, `mcp`, `dissect.esedb` |
| `README.md` | install steps + goose `srum` extension snippet + health-check + troubleshooting |

## 9. goose extension config (added to the Windows live config)
```yaml
  srum:
    type: streamable_http
    bundled: false
    name: srum
    enabled: true
    uri: http://127.0.0.1:8777/mcp
    headers: {}
    env_keys: []
    timeout: 120
    description: Windows SRUM + live system resource usage (CPU/mem/net/power)
```
(Also add to `config/windows_config.yaml` template so it survives re-deploys.)

## 10. Error handling
- Not elevated → SRUM tools return a structured error ("requires admin; start the server
  elevated") with `is_admin:false`; live tools still work.
- `SRUDB.dat` copy/parse failure → error with the esentutl/parse message; `srum_health` surfaces it.
- Empty window / table absent → empty list + note, not an exception.
- Port already in use → server exits with a clear "already running on :8777" message.

## 11. Testing
- `live_metrics.py`: unit tests assert dict shape, numeric ranges (0–100% cpu, non-negative rates).
- `srum_reader.py`: unit test against a **copied** SRUDB.dat (not the live one); assert IdMap
  resolves and at least App Resource Usage rows parse with sane timestamps.
- Integration: start the server, call `srum_health` + `live_snapshot` over HTTP; then end-to-end
  through goose (`goose run -t "call srum_health / live_snapshot"`).

## 12. Security
Loopback-only bind, no auth (matches local-tool model). The server is elevated, so it is kept
minimal and read-only (no tool mutates system state).

## 13. Out of scope (YAGNI)
- Cross-machine/LAN access (loopback only). Historical charting/graphs. Per-app energy alerting.
- Real-time per-app power on desktops (no OS sensor). Non-Windows support.

## 14. Open risks
- **SRUM energy schema varies by build** → resolved by the Task-1 spike; energy tool returns
  whatever the local schema provides, honestly labeled.
- `dissect.esedb` parsing edge cases on a dirty DB → mitigated by VSS copy + optional `esentutl /p`.
- CPU "time" is cycle counts in SRUM → reported as cycles, not seconds (documented in tool output).
