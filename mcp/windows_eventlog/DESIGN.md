# Windows Event Log MCP — Design Spec

> Date: 2026-06-29 · Status: approved (brainstorm) → ready for implementation plan
> Location: `HarnessAgent/mcp/windows_eventlog/` · Boundary: **never modifies `PersonalKnowledge-GB10`**
> Sibling of the SRUM MCP (`HarnessAgent/mcp/windows_srum/`); same architecture pattern.

## 1. Goal
Give the Windows goose harness tools to query the Windows **Event Log** for two scenarios:
- **System errors**: Error/Critical/Warning events from `System` / `Application` (and any channel).
- **User behavior**: logon/logoff/account events from the `Security` log.
Useful for incident analysis, "what went wrong", and "who did what / when".

## 2. Architecture
```
Windows machine (single host)
  goose (USER mode) ──streamable_http──▶ 127.0.0.1:8778/mcp
   └ extension: eventlog                    │
                                            ▼
                    eventlog_mcp_server.py  (ELEVATED / admin)
                      ├ eventlog_reader.py  (win32evtlog Evt API: EvtQuery+XPath, EvtRender, EvtFormatMessage)
                      └ curated.py          (user_activity event-ID map; error_summary logic)
```
- Server runs **elevated** so the `Security` log (user behavior) is readable. `System`/`Application`
  are readable unprivileged, but the single server runs elevated by design.
- goose runs **user mode**, talks over **loopback HTTP** — privilege decoupled (same as SRUM/DTM).
- Bind **`127.0.0.1:8778`** only. Transport **streamable HTTP** via the `mcp` SDK (FastMCP).
  goose extension: `type: streamable_http`, `uri: http://127.0.0.1:8778/mcp`
  (**Goose 1.39 dropped SSE** — use streamable_http/`/mcp`).

## 3. Constraints / assumptions
- Windows only. Python 3.13; `mcp` + `pywin32` (provides `win32evtlog`) already present.
- ~1290 channels available. Access via the **modern Evt API** (all channels + XPath), NOT the
  legacy `ReadEventLog` (classic logs only) and NOT `wevtutil`/`Get-WinEvent` subprocesses.
- All code under `HarnessAgent/mcp/windows_eventlog/`. No changes to `PersonalKnowledge-GB10`.
- Read-only: no tool writes/clears any log.

## 4. Tool surface (6)
- `list_channels(filter: str = "", limit: int = 100)` → `[{channel, record_count}]` across the 1290 channels.
- `query_events(channel="System", level=None, event_ids=None, provider=None, hours=24,
   keyword=None, max=50)` → `[event]` where
   `event = {time, channel, provider, event_id, level, record_id, computer, user, message, data}`.
   Filters compiled into a server-side **XPath** (time window, Level, EventID, Provider); `keyword`
   filters client-side on the rendered message/data.
- `error_summary(hours=24, channels=["System","Application"], include_warning=False, top_n=20)` →
   `{window_hours, groups:[{provider, event_id, level, count, latest_time, latest_message}]}`.
- `user_activity(hours=24, max=100)` → `{window_hours, events:[event]}` from `Security`, restricted to
   curated IDs (see §6). Returns a "needs admin" error if not elevated.
- `get_event(channel, record_id)` → one event with `message`, `data` (EventData k/v), and raw `xml`.
- `eventlog_health()` → `{is_admin, security_readable, channels_total, sample:{System,Application}, errors?}`.

All "query" tools cap results at `max`; levels use Windows numeric levels (1=Critical,2=Error,3=Warning,4=Info).

## 5. Reader approach (`eventlog_reader.py`)
1. Build an XPath query string from filters, e.g.
   `*[System[(Level=2) and (EventID=1000) and TimeCreated[timediff(@SystemTime) <= 86400000]]]`.
2. `h = win32evtlog.EvtQuery(channel, EvtQueryReverseDirection | EvtQueryChannelPath, xpath)`;
   page with `EvtNext(h, batch)`; `EvtRender(evt, EvtRenderEventXml)` → XML.
3. Parse XML (`xml.etree.ElementTree`, strip the `events` namespace) into the event dict:
   System fields (Provider/@Name, EventID, Level, TimeCreated/@SystemTime, EventRecordID, Computer,
   Security/@UserID) + EventData `Data` elements (named where possible) into `data`.
4. Human-readable `message`: `EvtFormatMessage` via `EvtOpenPublisherMetadata(provider)`
   (EvtFormatMessageEvent). **Fallback** to a compact rendering of `data` when no publisher
   metadata / formatting fails. *(Confirmed by a small message-rendering spike — Task 1.)*
- No DB copy / no cache needed (queries are parameterized and fast); `max` bounds work.

## 6. Curated event IDs (`curated.py`) — user_activity (Security)
4624 logon · 4625 failed logon · 4634 logoff · 4647 user-initiated logoff · 4648 explicit-cred logon ·
4672 special privileges assigned · 4720 account created · 4722 enabled · 4723/4724 password change/reset ·
4725 disabled · 4726 deleted · 4728/4732/4756 added to (global/local/universal) group · 4740 account lockout.
`error_summary` selects Level<=2 (Critical/Error), plus Level=3 when `include_warning=True`.

## 7. Persistence / startup
- `start_eventlog_mcp.ps1` — elevated launcher (admin self-check + clear message if not).
- `install_task.ps1` / `uninstall_task.ps1` — Scheduled Task "EventLog-MCP" ("highest privileges",
  at logon). Port 8778.

## 8. Files & responsibilities
| File | Responsibility |
|---|---|
| `eventlog_mcp_server.py` | FastMCP server; registers the 6 tools; serves 127.0.0.1:8778 |
| `eventlog_reader.py` | EvtQuery/XPath build + render + XML parse + message formatting (no MCP deps) |
| `curated.py` | user_activity ID map + error_summary helpers (no MCP deps) |
| `start_eventlog_mcp.ps1`, `install_task.ps1`, `uninstall_task.ps1` | run/persist |
| `requirements.txt` | `mcp`, `pywin32`, `pytest` |
| `README.md` | install + goose snippet + verify + gotchas |
| `tests/` | unit (reader/curated) + smoke (server tools registered) |

## 9. goose extension config (added to Windows live config + template)
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

## 10. Error handling
- Not elevated → `user_activity`/`eventlog_health` mark `security_readable:false`; Security queries
  return a structured "requires admin" error; System/Application queries still work.
- Invalid channel / bad XPath → structured error with the channel name and reason (no exception).
- Empty result → empty list + note. Oversized request → capped at `max`.

## 11. Testing
- `eventlog_reader.py`: unit tests assert `query_events("System", level=2, hours=168, max=5)` returns
  well-formed event dicts (event_id int, time parseable, message str).
- `curated.py`: `user_activity` builds the expected XPath/ID set; `error_summary` groups correctly.
- Smoke: server registers all 6 tools. Integration: MCP client handshake → `eventlog_health` +
  `query_events`; then end-to-end through goose.

## 12. Security
Loopback-only, no auth (local-tool model). Server elevated → kept minimal and strictly read-only.

## 13. Out of scope (YAGNI)
Cross-machine/LAN. Real-time event subscription/push. Writing/clearing logs. Log-size/retention admin.
Cross-correlation across SRUM + Event Log (future). Non-Windows.

## 14. Open risks
- **Message formatting** via `EvtFormatMessage` can fail for providers without registered metadata →
  resolved by Task-1 spike; fallback to EventData rendering keeps tools useful.
- XPath `timediff` window relies on provider TimeCreated; channels without it fall back to client-side
  time filtering on the parsed `TimeCreated`.
