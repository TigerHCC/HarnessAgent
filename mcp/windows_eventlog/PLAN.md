# Windows Event Log MCP — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an elevated, loopback HTTP MCP server giving the Windows goose harness Event Log query tools for system-error and user-behavior analysis.

**Architecture:** One elevated Python process (`eventlog_mcp_server.py`, FastMCP, streamable HTTP on `127.0.0.1:8778/mcp`) exposes 6 tools backed by the pywin32 `win32evtlog` modern Evt API (EvtQuery + XPath + EvtRender + EvtFormatMessage). User-mode goose connects over loopback so privilege is decoupled. See `DESIGN.md`.

**Tech Stack:** Python 3.13, `mcp` (FastMCP), `pywin32` (`win32evtlog`, `win32security`), `pytest`; PowerShell launchers.

## Global Constraints
- **Platform: Windows only.** All code under `HarnessAgent/mcp/windows_eventlog/`. **Never modify `PersonalKnowledge-GB10`.**
- **Bind `127.0.0.1:8778` only.** Transport **streamable HTTP**; goose uses `type: streamable_http`, `uri: http://127.0.0.1:8778/mcp` (**Goose 1.39 dropped SSE**).
- **Server runs elevated (admin)** — required for the Security log. System/Application work unprivileged; Security tools degrade gracefully when not admin.
- **Read-only**: no tool writes or clears any log.
- Levels are Windows numerics: 1=Critical, 2=Error, 3=Warning, 4=Information.
- Commits are **local only** — do NOT `git push` (origin is the GB10 box).

---

### Task 1: Scaffolding, deps, and message-rendering spike

**Files:**
- Create: `requirements.txt`, `.gitignore`, `spike_eventlog.py`, `SPIKE_NOTES.md`

**Interfaces:**
- Produces: confirmed facts for Task 2 — `EvtQuery` flag combo, `EvtNext` exhaustion behavior,
  `EvtFormatMessage` signature, and whether publisher metadata is generally available.

- [ ] **Step 1: Create `requirements.txt`**
```
mcp>=1.2
pywin32>=306
pytest>=8.0
```

- [ ] **Step 2: Create `.gitignore`**
```
__pycache__/
*.pyc
.pytest_cache/
```

- [ ] **Step 3: Install deps**

Run: `python -m pip install -r HarnessAgent/mcp/windows_eventlog/requirements.txt`
Expected: satisfied (pywin32 + mcp already present).

- [ ] **Step 4: Write `spike_eventlog.py`**
```python
"""Read-only Event Log API spike. Confirms Evt API usage + message formatting."""
import win32evtlog

def main():
    flags = win32evtlog.EvtQueryChannelPath | win32evtlog.EvtQueryReverseDirection
    xpath = "*[System[(Level=2 or Level=1)]]"  # errors/critical
    h = win32evtlog.EvtQuery("System", flags, xpath)
    got = 0
    while got < 3:
        try:
            evts = win32evtlog.EvtNext(h, 3)
        except Exception as e:
            print("EvtNext stop:", e); break
        if not evts:
            break
        for e in evts:
            xml = win32evtlog.EvtRender(e, win32evtlog.EvtRenderEventXml)
            print("\n--- XML head ---"); print(xml[:300])
            # provider
            import xml.etree.ElementTree as ET
            ns = "{http://schemas.microsoft.com/win/2004/08/events/event}"
            prov = ET.fromstring(xml).find(f"{ns}System/{ns}Provider").get("Name")
            try:
                meta = win32evtlog.EvtOpenPublisherMetadata(prov, None, 0, 0)
                msg = win32evtlog.EvtFormatMessage(meta, e, 0, None, win32evtlog.EvtFormatMessageEvent)
                print("MESSAGE:", (msg or "")[:200])
            except Exception as ex:
                print("FormatMessage err for", prov, ":", ex)
            got += 1
            if got >= 3: break

if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the spike (elevated)**

Run: `python HarnessAgent/mcp/windows_eventlog/spike_eventlog.py`
Expected: prints 3 System error events' XML + a human-readable MESSAGE for each (or a FormatMessage
err for some providers). **Record in `SPIKE_NOTES.md`: confirmed EvtNext exhaustion exception, the
exact `EvtFormatMessage(meta, evt, 0, None, EvtFormatMessageEvent)` call, and whether most providers
return a message.** Adjust Task 2's `_format_message` if the signature differs.

- [ ] **Step 6: Commit**
```bash
git add HarnessAgent/mcp/windows_eventlog/requirements.txt HarnessAgent/mcp/windows_eventlog/.gitignore HarnessAgent/mcp/windows_eventlog/spike_eventlog.py HarnessAgent/mcp/windows_eventlog/SPIKE_NOTES.md
git commit -m "feat(eventlog-mcp): scaffolding + Evt API/message spike"
```

---

### Task 2: `eventlog_reader.py` — query, XPath, parse, format

**Files:**
- Create: `eventlog_reader.py`
- Test: `tests/test_eventlog_reader.py`

**Interfaces:**
- Consumes: SPIKE_NOTES facts (EvtFormatMessage signature; EvtNext exhaustion).
- Produces:
  - `is_admin() -> bool`
  - `query_events(channel="System", level=None, event_ids=None, provider=None, hours=24, keyword=None, max=50) -> dict`
    (`{channel, count, events:[{time, channel, provider, event_id, level, record_id, computer, user, message, data}]}` or `{error, channel}`)
  - `list_channels(filter="", limit=100) -> dict` (`{count, channels:[str]}`)
  - `get_event(channel, record_id) -> dict` (one event + `xml`, or `{error}`)
  - `health() -> dict`

- [ ] **Step 1: Write the failing tests**
```python
# tests/test_eventlog_reader.py
import eventlog_reader as r

def test_query_system_errors_shape():
    res = r.query_events(channel="System", level=2, hours=720, max=5)
    assert "events" in res, res
    if res["events"]:
        e = res["events"][0]
        assert isinstance(e["event_id"], int)
        assert e["channel"] == "System"
        assert "time" in e and "provider" in e
        assert isinstance(e["data"], dict)

def test_list_channels_includes_core():
    res = r.list_channels(filter="", limit=2000)
    assert "channels" in res
    assert "System" in res["channels"] and "Application" in res["channels"]

def test_health_shape():
    h = r.health()
    for k in ("is_admin", "security_readable", "channels_total"):
        assert k in h

def test_build_xpath():
    x = r._build_xpath(level=[1, 2], event_ids=[4624], provider="Foo", hours=24)
    assert "Level=1 or Level=2" in x and "EventID=4624" in x
    assert "Provider[@Name='Foo']" in x and "timediff(@SystemTime) <= 86400000" in x
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd HarnessAgent/mcp/windows_eventlog && python -m pytest tests/test_eventlog_reader.py -q`
Expected: FAIL (`ModuleNotFoundError: eventlog_reader`).

- [ ] **Step 3: Implement `eventlog_reader.py`**
```python
"""Windows Event Log reader via the pywin32 modern Evt API. No MCP deps."""
import ctypes
import xml.etree.ElementTree as ET
import win32evtlog

try:
    import win32security
except Exception:  # pragma: no cover
    win32security = None

NS = "{http://schemas.microsoft.com/win/2004/08/events/event}"
LEVELS = {1: "Critical", 2: "Error", 3: "Warning", 4: "Information", 5: "Verbose", 0: "Information"}


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _build_xpath(level=None, event_ids=None, provider=None, hours=24):
    conds = []
    if hours:
        conds.append(f"TimeCreated[timediff(@SystemTime) <= {int(hours) * 3600 * 1000}]")
    if level is not None:
        levels = level if isinstance(level, (list, tuple)) else [level]
        conds.append("(" + " or ".join(f"Level={int(l)}" for l in levels) + ")")
    if event_ids:
        conds.append("(" + " or ".join(f"EventID={int(e)}" for e in event_ids) + ")")
    if provider:
        safe = str(provider).replace("'", "")
        conds.append(f"Provider[@Name='{safe}']")
    return "*" if not conds else "*[System[" + " and ".join(conds) + "]]"


def _sid_to_name(sid_str):
    if not sid_str or win32security is None:
        return sid_str
    try:
        sid = win32security.ConvertStringSidToSid(sid_str)
        name, domain, _ = win32security.LookupAccountSid(None, sid)
        return f"{domain}\\{name}" if domain else name
    except Exception:
        return sid_str


def _parse_event_xml(xml):
    root = ET.fromstring(xml)
    sysel = root.find(f"{NS}System")

    def find(tag):
        return sysel.find(f"{NS}{tag}") if sysel is not None else None

    prov = find("Provider")
    provider = prov.get("Name") if prov is not None else None
    eid_el, lvl_el, tc_el = find("EventID"), find("Level"), find("TimeCreated")
    rec_el, comp_el, sec_el = find("EventRecordID"), find("Computer"), find("Security")
    eid = (eid_el.text if eid_el is not None else None)
    lvl = (lvl_el.text if lvl_el is not None else None)
    data = {}
    ed = root.find(f"{NS}EventData")
    if ed is not None:
        for i, d in enumerate(ed.findall(f"{NS}Data")):
            data[d.get("Name") or f"Data{i}"] = d.text
    return {
        "time": tc_el.get("SystemTime") if tc_el is not None else None,
        "provider": provider,
        "event_id": int(eid) if eid and str(eid).isdigit() else eid,
        "level": LEVELS.get(int(lvl) if lvl and str(lvl).isdigit() else -1, lvl),
        "record_id": int(rec_el.text) if rec_el is not None and rec_el.text and rec_el.text.isdigit() else (rec_el.text if rec_el is not None else None),
        "computer": comp_el.text if comp_el is not None else None,
        "user": _sid_to_name(sec_el.get("UserID")) if sec_el is not None and sec_el.get("UserID") else None,
        "data": data,
    }


def _format_message(evt, provider):
    if not provider:
        return None
    try:
        meta = win32evtlog.EvtOpenPublisherMetadata(provider, None, 0, 0)
        msg = win32evtlog.EvtFormatMessage(meta, evt, 0, None, win32evtlog.EvtFormatMessageEvent)
        return (msg or "").strip() or None
    except Exception:
        return None


def _iter(channel, xpath, max_n, include_xml=False):
    flags = win32evtlog.EvtQueryChannelPath | win32evtlog.EvtQueryReverseDirection
    h = win32evtlog.EvtQuery(channel, flags, xpath)
    out = []
    while len(out) < max_n:
        try:
            evts = win32evtlog.EvtNext(h, min(64, max_n - len(out)))
        except Exception:
            break  # ERROR_NO_MORE_ITEMS
        if not evts:
            break
        for e in evts:
            xml = win32evtlog.EvtRender(e, win32evtlog.EvtRenderEventXml)
            rec = _parse_event_xml(xml)
            rec["channel"] = channel
            rec["message"] = _format_message(e, rec["provider"]) or (
                "; ".join(f"{k}={v}" for k, v in rec["data"].items() if v) or None)
            if include_xml:
                rec["xml"] = xml
            out.append(rec)
            if len(out) >= max_n:
                break
    return out


def query_events(channel="System", level=None, event_ids=None, provider=None, hours=24, keyword=None, max=50):
    try:
        xpath = _build_xpath(level, event_ids, provider, hours)
        fetch = int(max) * 5 if keyword else int(max)
        rows = _iter(channel, xpath, max(1, fetch))
        if keyword:
            k = keyword.lower()
            rows = [r for r in rows if k in (r.get("message") or "").lower() or k in str(r.get("data")).lower()]
        rows = rows[:int(max)]
        return {"channel": channel, "count": len(rows), "events": rows}
    except Exception as e:
        return {"error": str(e), "channel": channel}


def list_channels(filter="", limit=100):
    names = []
    try:
        h = win32evtlog.EvtOpenChannelEnum()
        while True:
            try:
                name = win32evtlog.EvtNextChannelPath(h)
            except Exception:
                break
            if not name:
                break
            if filter.lower() in name.lower():
                names.append(name)
        names.sort()
        return {"count": min(len(names), int(limit)), "channels": names[:int(limit)]}
    except Exception as e:
        return {"error": str(e)}


def get_event(channel, record_id):
    try:
        xpath = f"*[System[(EventRecordID={int(record_id)})]]"
        rows = _iter(channel, xpath, 1, include_xml=True)
        return rows[0] if rows else {"error": "not found", "channel": channel, "record_id": record_id}
    except Exception as e:
        return {"error": str(e), "channel": channel}


def health():
    h = {"is_admin": is_admin(), "security_readable": False, "channels_total": None}
    try:
        h["channels_total"] = len(list_channels(limit=100000).get("channels", []))
    except Exception as e:
        h["channels_error"] = str(e)
    h["sample"] = {"System": query_events("System", hours=168, max=1).get("count"),
                   "Application": query_events("Application", hours=168, max=1).get("count")}
    sec = query_events("Security", hours=168, max=1)
    h["security_readable"] = "events" in sec and "error" not in sec
    if "error" in sec:
        h["security_error"] = sec["error"]
    return h
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd HarnessAgent/mcp/windows_eventlog && python -m pytest tests/test_eventlog_reader.py -v`
Expected: PASS (4 passed). Fix `_format_message`/parse per `SPIKE_NOTES.md` if assertions fail.

- [ ] **Step 5: Commit**
```bash
git add HarnessAgent/mcp/windows_eventlog/eventlog_reader.py HarnessAgent/mcp/windows_eventlog/tests/test_eventlog_reader.py
git commit -m "feat(eventlog-mcp): Event Log reader (EvtQuery/XPath + render + parse)"
```

---

### Task 3: `curated.py` — user_activity + error_summary

**Files:**
- Create: `curated.py`
- Test: `tests/test_curated.py`

**Interfaces:**
- Consumes: `eventlog_reader.query_events`, `eventlog_reader.is_admin`.
- Produces:
  - `SECURITY_EVENT_IDS: dict[int, str]`
  - `user_activity(hours=24, max=100) -> dict`
  - `error_summary(hours=24, channels=("System","Application"), include_warning=False, top_n=20) -> dict`

- [ ] **Step 1: Write the failing tests**
```python
# tests/test_curated.py
import curated

def test_security_ids_present():
    assert 4624 in curated.SECURITY_EVENT_IDS and 4625 in curated.SECURITY_EVENT_IDS

def test_error_summary_shape():
    res = curated.error_summary(hours=720, top_n=5)
    assert "groups" in res and isinstance(res["groups"], list)
    if res["groups"]:
        g = res["groups"][0]
        assert {"provider", "event_id", "count"} <= set(g)
        assert g["count"] >= 1

def test_user_activity_admin_gated_or_events():
    res = curated.user_activity(hours=720, max=10)
    # either admin-gated error, or an events list
    assert ("events" in res) or (res.get("is_admin") is False)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd HarnessAgent/mcp/windows_eventlog && python -m pytest tests/test_curated.py -q`
Expected: FAIL (`ModuleNotFoundError: curated`).

- [ ] **Step 3: Implement `curated.py`**
```python
"""Curated Event Log scenarios: user behavior (Security) + system errors. No MCP deps."""
import eventlog_reader as reader

SECURITY_EVENT_IDS = {
    4624: "logon", 4625: "failed logon", 4634: "logoff", 4647: "user-initiated logoff",
    4648: "explicit-cred logon", 4672: "special privileges assigned", 4720: "account created",
    4722: "account enabled", 4723: "password change", 4724: "password reset",
    4725: "account disabled", 4726: "account deleted", 4728: "added to global group",
    4732: "added to local group", 4756: "added to universal group", 4740: "account lockout",
}


def user_activity(hours=24, max=100):
    if not reader.is_admin():
        return {"error": "Security log requires admin; start the server elevated.", "is_admin": False}
    res = reader.query_events(channel="Security", event_ids=list(SECURITY_EVENT_IDS),
                              hours=hours, max=max)
    for e in res.get("events", []):
        e["activity"] = SECURITY_EVENT_IDS.get(e.get("event_id"), "other")
    return {"window_hours": hours, **res}


def error_summary(hours=24, channels=("System", "Application"), include_warning=False, top_n=20):
    levels = [1, 2] + ([3] if include_warning else [])
    groups = {}
    for ch in channels:
        res = reader.query_events(channel=ch, level=levels, hours=hours, max=2000)
        for e in res.get("events", []):  # reverse-time order: first seen per key is latest
            key = (e.get("provider"), e.get("event_id"))
            g = groups.get(key)
            if g is None:
                g = groups[key] = {"provider": e.get("provider"), "event_id": e.get("event_id"),
                                   "level": e.get("level"), "channel": ch, "count": 0,
                                   "latest_time": e.get("time"), "latest_message": e.get("message")}
            g["count"] += 1
    ranked = sorted(groups.values(), key=lambda x: x["count"], reverse=True)[:int(top_n)]
    return {"window_hours": hours, "channels": list(channels), "groups": ranked}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd HarnessAgent/mcp/windows_eventlog && python -m pytest tests/test_curated.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**
```bash
git add HarnessAgent/mcp/windows_eventlog/curated.py HarnessAgent/mcp/windows_eventlog/tests/test_curated.py
git commit -m "feat(eventlog-mcp): curated user_activity + error_summary"
```

---

### Task 4: `eventlog_mcp_server.py` — FastMCP server

**Files:**
- Create: `eventlog_mcp_server.py`
- Test: `tests/test_server_smoke.py`

**Interfaces:**
- Consumes: `eventlog_reader.*`, `curated.*`.
- Produces: MCP server with tools `list_channels, query_events, error_summary, user_activity, get_event, eventlog_health` on `http://127.0.0.1:8778/mcp`.

- [ ] **Step 1: Write the failing smoke test**
```python
# tests/test_server_smoke.py
import eventlog_mcp_server as s

def test_tools_registered():
    names = set(s.list_tool_names())
    assert {"list_channels", "query_events", "error_summary",
            "user_activity", "get_event", "eventlog_health"} <= names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd HarnessAgent/mcp/windows_eventlog && python -m pytest tests/test_server_smoke.py -q`
Expected: FAIL (`ModuleNotFoundError: eventlog_mcp_server`).

- [ ] **Step 3: Implement `eventlog_mcp_server.py`**
```python
"""Windows Event Log MCP server (FastMCP, streamable HTTP, 127.0.0.1:8778).

Run ELEVATED for the Security log (System/Application work either way). Goose connects via
type: streamable_http, uri: http://127.0.0.1:8778/mcp  (Goose 1.39 dropped SSE).
"""
from mcp.server.fastmcp import FastMCP

import eventlog_reader as reader
import curated

mcp = FastMCP("eventlog", host="127.0.0.1", port=8778)


@mcp.tool()
def list_channels(filter: str = "", limit: int = 100) -> dict:
    """List available Event Log channels (optionally filtered by substring)."""
    return reader.list_channels(filter=filter, limit=limit)


@mcp.tool()
def query_events(channel: str = "System", level: int = None, event_ids: list = None,
                 provider: str = None, hours: int = 24, keyword: str = None, max: int = 50) -> dict:
    """Query events from a channel with filters (level 1=Crit 2=Err 3=Warn 4=Info, event_ids, provider, time window, keyword)."""
    return reader.query_events(channel=channel, level=level, event_ids=event_ids,
                               provider=provider, hours=hours, keyword=keyword, max=max)


@mcp.tool()
def error_summary(hours: int = 24, channels: list = None, include_warning: bool = False, top_n: int = 20) -> dict:
    """System errors: Error/Critical events grouped by (provider, event_id) with counts + latest message."""
    return curated.error_summary(hours=hours, channels=tuple(channels) if channels else ("System", "Application"),
                                 include_warning=include_warning, top_n=top_n)


@mcp.tool()
def user_activity(hours: int = 24, max: int = 100) -> dict:
    """User behavior: curated Security logon/logoff/account events (needs admin)."""
    return curated.user_activity(hours=hours, max=max)


@mcp.tool()
def get_event(channel: str, record_id: int) -> dict:
    """Full detail (message + EventData + raw XML) of one event by record id."""
    return reader.get_event(channel, record_id)


@mcp.tool()
def eventlog_health() -> dict:
    """Admin status, Security readability, total channels, sample counts."""
    return reader.health()


def list_tool_names():
    import asyncio
    return [t.name for t in asyncio.run(mcp.list_tools())]


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd HarnessAgent/mcp/windows_eventlog && python -m pytest tests/test_server_smoke.py -v`
Expected: PASS.

- [ ] **Step 5: Manual MCP client check (elevated)**

Start the server: `python HarnessAgent/mcp/windows_eventlog/eventlog_mcp_server.py` (background).
Then run a short MCP client that initializes, lists tools, and calls `eventlog_health` +
`query_events(channel="System", level=2, hours=168, max=3)` against `http://127.0.0.1:8778/mcp`.
Expected: 6 tools; `eventlog_health` shows `is_admin: true`, `security_readable: true`, `channels_total ~1290`;
`query_events` returns System error events with messages. Stop the server.

- [ ] **Step 6: Commit**
```bash
git add HarnessAgent/mcp/windows_eventlog/eventlog_mcp_server.py HarnessAgent/mcp/windows_eventlog/tests/test_server_smoke.py
git commit -m "feat(eventlog-mcp): FastMCP server exposing the 6 tools"
```

---

### Task 5: Elevated launcher + scheduled task

**Files:**
- Create: `start_eventlog_mcp.ps1`, `install_task.ps1`, `uninstall_task.ps1`

- [ ] **Step 1: Write `start_eventlog_mcp.ps1`**
```powershell
# Starts the Event Log MCP server elevated. Run as Administrator.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) {
  Write-Host "[X] Must run elevated. Open PowerShell as Administrator, then re-run this script." -ForegroundColor Red
  Write-Host "    (The Security log needs admin; System/Application would work but the server runs elevated by design.)" -ForegroundColor Yellow
  exit 1
}
$py = (Get-Command python).Source
$env:PYTHONIOENCODING = "utf-8"
Write-Host "[*] Starting Event Log MCP on http://127.0.0.1:8778/mcp  (python: $py)" -ForegroundColor Cyan
& $py (Join-Path $here "eventlog_mcp_server.py")
```

- [ ] **Step 2: Write `install_task.ps1`**
```powershell
# Registers a Scheduled Task that runs the Event Log MCP server elevated at logon. Run as Administrator.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) { Write-Host "[X] Run this elevated (Administrator)." -ForegroundColor Red; exit 1 }
$py = (Get-Command python).Source
$server = Join-Path $here "eventlog_mcp_server.py"
$action = New-ScheduledTaskAction -Execute $py -Argument "`"$server`"" -WorkingDirectory $here
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -RunLevel Highest -LogonType Interactive
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "EventLog-MCP" -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "[OK] Registered scheduled task 'EventLog-MCP' (elevated, at logon). Start now: Start-ScheduledTask -TaskName EventLog-MCP" -ForegroundColor Green
```

- [ ] **Step 3: Write `uninstall_task.ps1`**
```powershell
Unregister-ScheduledTask -TaskName "EventLog-MCP" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "[OK] Removed scheduled task 'EventLog-MCP' (if it existed)." -ForegroundColor Green
```

- [ ] **Step 4: Syntax-check all three**

Run: parse each with `[System.Management.Automation.Language.Parser]::ParseFile(...)`.
Expected: PARSE OK for all three. (Non-admin guard verified by inspection — same pattern as SRUM.)

- [ ] **Step 5: Commit**
```bash
git add HarnessAgent/mcp/windows_eventlog/start_eventlog_mcp.ps1 HarnessAgent/mcp/windows_eventlog/install_task.ps1 HarnessAgent/mcp/windows_eventlog/uninstall_task.ps1
git commit -m "feat(eventlog-mcp): elevated launcher + scheduled-task persistence"
```

---

### Task 6: Wire into goose + README + end-to-end

**Files:**
- Modify: `config/windows_config.yaml` (+ deploy to `%APPDATA%\Block\goose\config\config.yaml`)
- Create: `README.md`

- [ ] **Step 1: Add the `eventlog` extension to `config/windows_config.yaml`** under `extensions:`
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

- [ ] **Step 2: Deploy to the live config + refresh backup**

Run:
`Copy-Item HarnessAgent/config/windows_config.yaml "$env:APPDATA\Block\goose\config\config.yaml" -Force`
`Copy-Item "$env:APPDATA\Block\goose\config\config.yaml" "$env:APPDATA\Block\goose\config\config.yaml.bak" -Force`

- [ ] **Step 3: Write `README.md`**
```markdown
# Windows Event Log MCP
Elevated loopback MCP server giving goose Event Log query tools (system errors + user behavior).

## Install
1. `python -m pip install -r requirements.txt`
2. (one-time, Administrator) `.\install_task.ps1`  — auto-starts elevated at logon
   OR on demand (Administrator): `.\start_eventlog_mcp.ps1`
3. The `eventlog` extension is in config/windows_config.yaml; deploy it to the live config.

## Tools
list_channels, query_events, error_summary, user_activity, get_event, eventlog_health.

## Verify
- `GOOSE_MODE=auto goose run --no-session -t "call eventlog_health and report it"`
- `... -t "use error_summary for the last 48 hours and list the top 5 system errors"`

## Notes
- Server MUST be elevated for the Security log (user_activity). System/Application work either way.
- Goose 1.39 uses streamable_http (/mcp), NOT sse. Server is read-only (never writes/clears logs).
- Levels: 1=Critical 2=Error 3=Warning 4=Information.
```

- [ ] **Step 4: End-to-end through goose** (server running elevated)

Run: `cd HarnessAgent; $env:GOOSE_MODE="auto"; goose run --no-session --max-turns 4 -t "Call eventlog_health, then error_summary for the last 72 hours (top 5). Report admin status, channels_total, and the top error sources."`
Expected: goose shows `▸ eventlog_health eventlog` and `▸ error_summary eventlog` blocks and reports real values.

- [ ] **Step 5: Commit**
```bash
git add HarnessAgent/config/windows_config.yaml HarnessAgent/mcp/windows_eventlog/README.md
git commit -m "feat(eventlog-mcp): wire eventlog extension into goose config + README"
```

---

## Self-Review
- **Spec coverage:** reader query/list/get/health (Task 2) ✓; curated user_activity + error_summary (Task 3) ✓; FastMCP streamable_http server on 8778 with 6 tools (Task 4) ✓; launcher + scheduled task (Task 5) ✓; goose wiring + README + e2e (Task 6) ✓; message-format spike (Task 1) ✓; non-admin Security degradation (`curated.user_activity`, `reader.health`) ✓; loopback + read-only + SSE caveat (Global Constraints) ✓; curated Security IDs (Task 3 SECURITY_EVENT_IDS) match spec §6 ✓.
- **Placeholders:** none — full code in every code step. Spike-confirmed spot (`_format_message` signature) has a working default + explicit "adjust per SPIKE_NOTES.md".
- **Type consistency:** tool names match between Task 4 server and Task 6 config (`eventlog`); reader function names (`query_events/list_channels/get_event/health/is_admin`) consistent across Tasks 2–4; curated names (`user_activity/error_summary/SECURITY_EVENT_IDS`) consistent across Tasks 3–4.
- **Known deviation from spec §4:** `list_channels` returns channel names only (no per-channel `record_count`) — counting across 1290 channels is too expensive; use `query_events` to count a specific channel. Documented in code/README.
