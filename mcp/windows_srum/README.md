# Windows SRUM MCP

An **elevated, loopback** MCP server that gives the Windows goose harness:
- **Live** (real-time): `live_snapshot`, `top_processes` — CPU / memory / disk / network / power.
- **SRUM** (historical, per-app): `srum_app_usage`, `srum_network_usage`, `srum_energy_usage`,
  `srum_health` — parsed from `C:\Windows\System32\sru\SRUDB.dat`.

goose (user mode) talks to it over `http://127.0.0.1:8777/mcp` (streamable HTTP), so the server
can run elevated for SRUM while goose stays unprivileged. See `DESIGN.md` / `PLAN.md`.

## Install
```powershell
cd HarnessAgent\mcp\windows_srum
python -m pip install -r requirements.txt
```

## Run (the server must be ELEVATED for SRUM)
- On demand: right-click PowerShell → **Run as Administrator**, then:
  ```powershell
  .\start_srum_mcp.ps1
  ```
- Persistent (auto-start elevated at logon): run once as Administrator:
  ```powershell
  .\install_task.ps1      # registers scheduled task 'SRUM-MCP'
  Start-ScheduledTask -TaskName SRUM-MCP   # start it now
  ```
  Remove with `.\uninstall_task.ps1`.

## Wire into goose
The `srum` extension is already in `config/windows_config.yaml`:
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
    description: Windows SRUM + live system resource usage (CPU/mem/net/power) via local elevated MCP server (127.0.0.1:8777)
```
Deploy it to the live config goose reads:
```powershell
Copy-Item ..\..\config\windows_config.yaml "$env:APPDATA\Block\goose\config\config.yaml" -Force
```

## Verify
```powershell
$env:GOOSE_MODE="auto"
goose run --no-session -t "Call srum_health, then live_snapshot. Report admin status, SRUM tables, and current CPU%/memory%/battery."
goose run --no-session -t "Use srum_network_usage for the last 48 hours and list the top 5 apps by bytes."
```

## Notes / gotchas
- **Server MUST be elevated** for SRUM (reads the locked, admin-only `SRUDB.dat` via
  `esentutl /vss`). Live tools work either way, but the single server runs elevated by design.
- **SRUM is historical (~hourly flush)**, not real-time. For "right now" use `live_snapshot`.
- **Per-app energy** (`srum_energy_usage`) is often **0** on desktops (the OS doesn't populate
  per-app energy estimation) — reported honestly. Per-app **CPU** is in *cycle counts*, not seconds.
- **Live wattage**: only available on laptops (battery discharge mW via WMI); `null` on desktops.
- Goose 1.39 uses `streamable_http` + `/mcp` (NOT `sse`). A raw `GET /mcp` returning 400 is normal.
- First SRUM call copies the 94 MB DB (~3 s) then caches for 10 min; repeat queries are fast.

## Files
| File | Purpose |
|---|---|
| `srum_mcp_server.py` | FastMCP server (registers the 6 tools, serves 127.0.0.1:8777) |
| `live_metrics.py` | live metrics (psutil + WMI battery) |
| `srum_reader.py` | SRUM copy (esentutl/VSS) + parse (dissect.esedb) + cache |
| `start_srum_mcp.ps1` | elevated launcher |
| `install_task.ps1` / `uninstall_task.ps1` | scheduled-task persistence |
| `requirements.txt` | python deps |
| `SCHEMA.md` | confirmed SRUM schema + decoding notes |
| `tests/` | pytest unit + smoke tests |

Batch-test this server with the [central MCP suite instructions](../README.md#test-all-local-mcp-servers).
