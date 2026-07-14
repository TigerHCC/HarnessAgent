# Windows Event Log MCP

An **elevated, loopback** MCP server giving the Windows goose harness Event Log query tools for
**system-error** and **user-behavior** analysis (parsed from the Windows Event Log via the
pywin32 modern Evt API). goose (user mode) connects over `http://127.0.0.1:8778/mcp`
(streamable HTTP) so the server can run elevated for the Security log while goose stays
unprivileged. See `DESIGN.md` / `PLAN.md`.

## Tools (6)
| Tool | Purpose |
|---|---|
| `list_channels(filter, limit)` | discover among ~1290 channels |
| `query_events(channel, level, event_ids, provider, hours, keyword, max)` | flexible filtered query (level 1=Crit 2=Err 3=Warn 4=Info) |
| `error_summary(hours, channels, include_warning, top_n)` | **system errors**: Error/Critical grouped by (provider, event_id) + counts |
| `user_activity(hours, max)` | **user behavior**: curated Security logon/logoff/account events (needs admin) |
| `get_event(channel, record_id)` | full detail (message + EventData + raw XML) of one event |
| `eventlog_health()` | admin status, Security readability, channel count, samples |

## Install
```powershell
cd HarnessAgent\mcp\windows_eventlog
python -m pip install -r requirements.txt
```

## Run (ELEVATED for the Security log)
- On demand: PowerShell **as Administrator** → `.\start_eventlog_mcp.ps1`
- Persistent (auto-start elevated at logon): as Administrator → `.\install_task.ps1` then
  `Start-ScheduledTask -TaskName EventLog-MCP`. Remove with `.\uninstall_task.ps1`.

## Wire into goose
The `eventlog` extension is in `config/windows_config.yaml`:
```yaml
  eventlog:
    type: streamable_http
    bundled: false
    name: eventlog
    enabled: true
    uri: http://127.0.0.1:8778/mcp
    headers: {}
    env_keys: []
    timeout: 120
    description: Windows Event Log (system errors + user behavior) via local elevated MCP server (127.0.0.1:8778)
```
Deploy it to the live config:
```powershell
Copy-Item ..\..\config\windows_config.yaml "$env:APPDATA\Block\goose\config\config.yaml" -Force
```

## Verify
```powershell
$env:GOOSE_MODE="auto"
goose run --no-session -t "Call eventlog_health, then error_summary for the last 72 hours (top 5 system errors)."
goose run --no-session -t "Use user_activity for the last 24 hours and summarize logons."
```

## Notes / gotchas
- **Server MUST be elevated** for the Security log (`user_activity`). System/Application work either way.
- **Read-only** — no tool writes or clears any log.
- Goose 1.39 uses `streamable_http` + `/mcp` (NOT `sse`).
- Event **messages are localized** to the system display language (e.g. zh-TW here); providers
  without registered metadata fall back to an `EventData` key=value rendering.
- `list_channels` returns names only (no per-channel counts — counting 1290 channels is too slow;
  use `query_events` to count a specific channel).
- `level` uses Windows numerics: 1=Critical, 2=Error, 3=Warning, 4=Information.

## Files
| File | Purpose |
|---|---|
| `eventlog_mcp_server.py` | FastMCP server (6 tools, serves 127.0.0.1:8778) |
| `eventlog_reader.py` | EvtQuery/XPath + render + XML parse + message formatting |
| `curated.py` | user_activity ID map + error_summary |
| `start_eventlog_mcp.ps1`, `install_task.ps1`, `uninstall_task.ps1` | run / persist |
| `requirements.txt` | mcp, pywin32 |
| `SPIKE_NOTES.md` | confirmed Evt API signatures |
| `tests/` | pytest unit + smoke tests |

Batch-test this server with the [central MCP suite instructions](../README.md#test-all-local-mcp-servers).
