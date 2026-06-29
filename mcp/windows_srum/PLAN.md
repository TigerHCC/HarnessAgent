# Windows SRUM MCP — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an elevated, loopback HTTP MCP server that gives the Windows goose harness live system metrics (CPU/mem/net/power) and SRUM-based historical per-app resource/energy usage.

**Architecture:** A single elevated Python process (`srum_mcp_server.py`, FastMCP, streamable HTTP on `127.0.0.1:8777/mcp`) exposes live tools (psutil/WMI) and SRUM tools (esentutl copy → `dissect.esedb` parse). User-mode goose connects over loopback HTTP so privilege is decoupled. See `DESIGN.md`.

**Tech Stack:** Python 3.13, `mcp` (FastMCP), `psutil`, `dissect.esedb`, WMI (via `wmi`/COM or `Get-CimInstance` fallback), `pytest`; PowerShell for launchers/scheduled task.

## Global Constraints
- **Platform: Windows only.** All code under `HarnessAgent/mcp/windows_srum/`. **Never modify `PersonalKnowledge-GB10`.**
- **Bind `127.0.0.1:8777` only** (loopback). Transport **streamable HTTP**; goose uses `type: streamable_http`, `uri: http://127.0.0.1:8777/mcp` (**Goose 1.39 dropped SSE — do not use `type: sse`**).
- **Server runs elevated (admin)** — required for SRUM. Live tools work regardless; SRUM tools must degrade gracefully when not admin.
- **Read-only**: no tool mutates system state.
- All model/host/port values are literals from this plan; SRUM CPU is reported as **cycle counts**, not seconds.
- Commits are **local only** — do NOT `git push` (origin is the GB10 box; pushing is a separate, explicit, user-approved step).

---

### Task 1: Scaffolding, dependencies, and SRUM schema spike

**Files:**
- Create: `HarnessAgent/mcp/windows_srum/requirements.txt`
- Create: `HarnessAgent/mcp/windows_srum/.gitignore`
- Create: `HarnessAgent/mcp/windows_srum/spike_srum.py`
- Create: `HarnessAgent/mcp/windows_srum/SCHEMA.md` (output of the spike)

**Interfaces:**
- Produces: confirmed facts for Task 3 — exact `dissect.esedb` record-access API, the present SRUM table GUIDs, the `SruDbIdMapTable` columns, and the timestamp column name/format.

- [ ] **Step 1: Create `requirements.txt`**
```
mcp>=1.2
psutil>=5.9
dissect.esedb>=3.0
wmi>=1.5
pytest>=8.0
```

- [ ] **Step 2: Create `.gitignore`** (never commit DB copies, caches, pyc)
```
__pycache__/
*.pyc
*.dat
.cache/
tests/fixtures/SRUDB_copy.dat
```

- [ ] **Step 3: Install deps**

Run: `python -m pip install -r HarnessAgent/mcp/windows_srum/requirements.txt`
Expected: all install OK (`dissect.esedb`, `psutil`, `mcp`, `wmi`, `pytest`).

- [ ] **Step 4: Write `spike_srum.py`** (copies the locked DB, lists tables + columns + 2 sample rows)
```python
"""Read-only SRUM schema spike. Run ELEVATED. Writes findings to stdout."""
import os, subprocess, tempfile, sys
from dissect.esedb import EseDB

SRUDB = os.path.join(os.environ["SystemRoot"], "System32", "sru", "SRUDB.dat")

def copy_locked(dst):
    # VSS copy handles the live lock; fall back to plain /y
    for args in (["esentutl.exe", "/y", SRUDB, "/vss", "/d", dst],
                 ["esentutl.exe", "/y", SRUDB, "/d", dst]):
        r = subprocess.run(args, capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(dst):
            return True, " ".join(args)
        last = r.stdout + r.stderr
    print("COPY FAILED:", last); return False, last

def main():
    tmp = os.path.join(tempfile.gettempdir(), "SRUDB_spike.dat")
    ok, how = copy_locked(tmp)
    if not ok: sys.exit(1)
    print("copied via:", how)
    with open(tmp, "rb") as fh:
        db = EseDB(fh)
        for t in db.tables():
            try:
                cols = [c.name for c in t.columns]
            except Exception as e:
                cols = [f"<cols err: {e}>"]
            print(f"\n=== TABLE {t.name} | cols={cols}")
            n = 0
            for rec in t.records():
                vals = {c: _safe(rec, c) for c in cols[:8]}
                print("  row:", vals); n += 1
                if n >= 2: break

def _safe(rec, col):
    try: return rec.get(col)
    except Exception as e: return f"<err {e}>"

if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the spike (elevated)**

Run (in an **elevated** PowerShell): `python HarnessAgent/mcp/windows_srum/spike_srum.py > HarnessAgent/mcp/windows_srum/SCHEMA.md 2>&1`
Expected: `SCHEMA.md` lists `SruDbIdMapTable`, `{D10CA2FE-6FCF-4F6D-848E-B2E99266FA89}` (App Resource Usage), `{973F5D5C-1D90-4944-BE8E-24B94231A174}` (Network Data Usage), `{FEE4E14F-02A9-4550-B5CE-5FA2DA202E37}` (Energy) with their columns + sample rows. **Record in SCHEMA.md the confirmed: (a) record access API (`rec.get(col)` vs attribute), (b) AppId/UserId column names, (c) the timestamp column name + whether it's OLE-date or FILETIME, (d) byte/cycle column names.**

- [ ] **Step 6: Commit**
```bash
git add HarnessAgent/mcp/windows_srum/requirements.txt HarnessAgent/mcp/windows_srum/.gitignore HarnessAgent/mcp/windows_srum/spike_srum.py HarnessAgent/mcp/windows_srum/SCHEMA.md
git commit -m "feat(srum-mcp): scaffolding + SRUM schema spike"
```

---

### Task 2: `live_metrics.py` — live system metrics

**Files:**
- Create: `HarnessAgent/mcp/windows_srum/live_metrics.py`
- Test: `HarnessAgent/mcp/windows_srum/tests/test_live_metrics.py`

**Interfaces:**
- Produces:
  - `snapshot() -> dict` with keys `cpu, memory, disk_io, network, power, uptime_seconds, top_cpu, top_mem`
  - `top_processes(by: str = "cpu", n: int = 10) -> list[dict]` (`by` in {"cpu","memory"})

- [ ] **Step 1: Write the failing tests**
```python
# tests/test_live_metrics.py
import live_metrics as lm

def test_snapshot_shape():
    s = lm.snapshot()
    for k in ("cpu","memory","disk_io","network","power","uptime_seconds","top_cpu","top_mem"):
        assert k in s
    assert 0 <= s["cpu"]["percent_total"] <= 100
    assert isinstance(s["cpu"]["percent_per_core"], list) and s["cpu"]["percent_per_core"]
    assert 0 <= s["memory"]["percent"] <= 100
    assert s["memory"]["total"] > 0
    assert s["network"]["total_recv_per_s"] >= 0
    assert s["uptime_seconds"] > 0

def test_top_processes():
    procs = lm.top_processes(by="memory", n=5)
    assert 1 <= len(procs) <= 5
    assert {"pid","name"} <= set(procs[0])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd HarnessAgent/mcp/windows_srum && python -m pytest tests/test_live_metrics.py -v`
Expected: FAIL (`ModuleNotFoundError: live_metrics` / attribute errors).

- [ ] **Step 3: Implement `live_metrics.py`**
```python
"""Live system metrics via psutil (+ WMI battery discharge). Pure functions, no MCP deps."""
import time, psutil

def _net_disk_rates(interval=0.5):
    n0, d0 = psutil.net_io_counters(pernic=True), psutil.disk_io_counters()
    t0 = time.time()
    time.sleep(interval)
    n1, d1 = psutil.net_io_counters(pernic=True), psutil.disk_io_counters()
    dt = max(time.time() - t0, 1e-3)
    nics = []
    tot_s = tot_r = 0.0
    for name, c1 in n1.items():
        c0 = n0.get(name)
        if not c0: continue
        s = (c1.bytes_sent - c0.bytes_sent) / dt
        r = (c1.bytes_recv - c0.bytes_recv) / dt
        nics.append({"name": name, "sent_bytes_per_s": round(s,1), "recv_bytes_per_s": round(r,1)})
        tot_s += s; tot_r += r
    disk = {"read_bytes_per_s": 0.0, "write_bytes_per_s": 0.0}
    if d0 and d1:
        disk = {"read_bytes_per_s": round((d1.read_bytes-d0.read_bytes)/dt,1),
                "write_bytes_per_s": round((d1.write_bytes-d0.write_bytes)/dt,1)}
    return nics, round(tot_s,1), round(tot_r,1), disk

def _battery_discharge_mw():
    """mW discharge rate via WMI root\\wmi BatteryStatus; None if unavailable/desktop/on AC."""
    try:
        import wmi
        w = wmi.WMI(namespace="root\\wmi")
        for b in w.BatteryStatus():
            dr = getattr(b, "DischargeRate", 0) or 0
            if dr: return int(dr)
    except Exception:
        return None
    return None

def _power():
    batt = psutil.sensors_battery()
    if batt is None:
        return {"battery_percent": None, "plugged_in": None, "secs_left": None, "discharge_rate_mw": None}
    secs = None if batt.secsleft in (psutil.POWER_TIME_UNKNOWN, psutil.POWER_TIME_UNLIMITED) else batt.secsleft
    return {"battery_percent": round(batt.percent,1), "plugged_in": bool(batt.power_plugged),
            "secs_left": secs, "discharge_rate_mw": _battery_discharge_mw()}

def top_processes(by="cpu", n=10):
    procs = []
    for p in psutil.process_iter(["pid","name","memory_info","memory_percent"]):
        try:
            cpu = p.cpu_percent(None)  # since last call; warmed below
            info = p.info
            procs.append({"pid": info["pid"], "name": info["name"] or "?",
                          "cpu": cpu,
                          "rss": getattr(info.get("memory_info"), "rss", 0),
                          "mem%": round(info.get("memory_percent") or 0, 2)})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    key = "cpu" if by == "cpu" else "rss"
    procs.sort(key=lambda x: x[key], reverse=True)
    return procs[:max(1, n)]

def snapshot():
    psutil.cpu_percent(None)  # prime
    for p in psutil.process_iter():  # prime per-proc cpu counters
        try: p.cpu_percent(None)
        except Exception: pass
    nics, tot_s, tot_r, disk = _net_disk_rates()
    per_core = psutil.cpu_percent(percpu=True)
    vm, sm = psutil.virtual_memory(), psutil.swap_memory()
    return {
        "cpu": {"percent_total": round(sum(per_core)/len(per_core),1) if per_core else psutil.cpu_percent(),
                "percent_per_core": [round(c,1) for c in per_core]},
        "memory": {"total": vm.total, "available": vm.available, "used": vm.used,
                   "percent": vm.percent, "swap_total": sm.total, "swap_used": sm.used},
        "disk_io": disk,
        "network": {"per_nic": nics, "total_sent_per_s": tot_s, "total_recv_per_s": tot_r},
        "power": _power(),
        "uptime_seconds": round(time.time() - psutil.boot_time()),
        "top_cpu": top_processes("cpu", 5),
        "top_mem": top_processes("memory", 5),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd HarnessAgent/mcp/windows_srum && python -m pytest tests/test_live_metrics.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**
```bash
git add HarnessAgent/mcp/windows_srum/live_metrics.py HarnessAgent/mcp/windows_srum/tests/test_live_metrics.py
git commit -m "feat(srum-mcp): live system metrics (psutil + WMI battery)"
```

---

### Task 3: `srum_reader.py` — SRUM copy, parse, aggregate, cache

**Files:**
- Create: `HarnessAgent/mcp/windows_srum/srum_reader.py`
- Test: `HarnessAgent/mcp/windows_srum/tests/test_srum_reader.py`

**Interfaces:**
- Consumes: confirmed schema facts from Task 1 `SCHEMA.md` (record access API, column names, timestamp format). If the spike showed attribute access instead of `.get()`, adjust `_cell()` accordingly.
- Produces:
  - `is_admin() -> bool`
  - `health() -> dict` (`srudb_path,size_mb,last_modified,is_admin,tables_found,row_counts,parser_ok,cache_age_s,error?`)
  - `app_usage(hours=24, top_n=20) -> list[dict]` (`app, foreground_cycles, background_cycles, bytes_read, bytes_written`)
  - `network_usage(hours=24, top_n=20) -> list[dict]` (`app, bytes_sent, bytes_recvd`)
  - `energy_usage(hours=24, top_n=20) -> list[dict]` (best-effort per local schema; always returns a list)

- [ ] **Step 1: Write the failing tests** (skip cleanly when no fixture/parser)
```python
# tests/test_srum_reader.py
import os, pytest, srum_reader as sr

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "SRUDB_copy.dat")
pytestmark = pytest.mark.skipif(not os.path.exists(FIX), reason="no SRUDB fixture (run spike Step 5 + copy to tests/fixtures/SRUDB_copy.dat)")

def test_parse_app_usage_from_fixture():
    rows = sr._parse(FIX)["app_usage"]
    assert isinstance(rows, list)
    if rows:
        r = rows[0]
        assert "app" in r and isinstance(r["app"], str)
        assert r["bytes_read"] >= 0 and r["bytes_written"] >= 0

def test_health_shape():
    h = sr.health()
    for k in ("srudb_path","is_admin","parser_ok"):
        assert k in h
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd HarnessAgent/mcp/windows_srum && python -m pytest tests/test_srum_reader.py -v`
Expected: FAIL (`ModuleNotFoundError: srum_reader`).

- [ ] **Step 3: Implement `srum_reader.py`** (adjust `_cell`/column names per `SCHEMA.md`)
```python
"""SRUM reader: copy locked SRUDB.dat, parse with dissect.esedb, aggregate per app, cache."""
import os, ctypes, subprocess, tempfile, time, datetime as dt
from dissect.esedb import EseDB

SRUDB = os.path.join(os.environ["SystemRoot"], "System32", "sru", "SRUDB.dat")
IDMAP = "SruDbIdMapTable"
T_APP = "{D10CA2FE-6FCF-4F6D-848E-B2E99266FA89}"   # App Resource Usage
T_NET = "{973F5D5C-1D90-4944-BE8E-24B94231A174}"   # Network Data Usage
T_ENERGY = "{FEE4E14F-02A9-4550-B5CE-5FA2DA202E37}"  # Energy Usage
_CACHE = {"ts": 0.0, "data": None, "copy": None}
_TTL = 600  # 10 min

def is_admin():
    try: return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception: return False

def _cell(rec, col):
    # Per SCHEMA.md spike: dissect.esedb records support .get(col). Adjust if attribute access.
    try: return rec.get(col)
    except Exception:
        try: return getattr(rec, col)
        except Exception: return None

def _copy_locked():
    dst = os.path.join(tempfile.gettempdir(), "SRUDB_mcp.dat")
    for args in (["esentutl.exe","/y",SRUDB,"/vss","/d",dst], ["esentutl.exe","/y",SRUDB,"/d",dst]):
        r = subprocess.run(args, capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(dst): return dst
    raise RuntimeError(f"esentutl copy failed: {r.stdout}{r.stderr}")

def _idmap(db):
    m = {}
    for rec in db.table(IDMAP).records():
        idx, blob = _cell(rec, "IdIndex"), _cell(rec, "IdBlob")
        if idx is None: continue
        name = blob
        if isinstance(blob, (bytes, bytearray)):
            try: name = blob.decode("utf-16-le", "ignore").strip("\x00") or blob.hex()
            except Exception: name = blob.hex()
        m[idx] = name
    return m

def _ts(rec):
    # SRUM 'TimeStamp' is OLE automation date (days since 1899-12-30). Confirm in SCHEMA.md.
    v = _cell(rec, "TimeStamp")
    if v is None: return None
    try: return dt.datetime(1899,12,30) + dt.timedelta(days=float(v))
    except Exception: return None

def _agg(db, table, idmap, hours, fields):
    cutoff = dt.datetime.utcnow() - dt.timedelta(hours=hours)
    out = {}
    if table not in [t.name for t in db.tables()]: return []
    for rec in db.table(table).records():
        ts = _ts(rec)
        if ts is not None and ts < cutoff: continue
        app = idmap.get(_cell(rec, "AppId"), f"id:{_cell(rec,'AppId')}")
        agg = out.setdefault(app, {"app": app, **{k: 0 for k in fields.values()}})
        for col, key in fields.items():
            agg[key] += int(_cell(rec, col) or 0)
    return sorted(out.values(), key=lambda r: sum(v for k,v in r.items() if k!="app"), reverse=True)

def _parse(path, hours=24):
    with open(path, "rb") as fh:
        db = EseDB(fh)
        idmap = _idmap(db)
        return {
            "app_usage": _agg(db, T_APP, idmap, hours,
                {"ForegroundCycleTime":"foreground_cycles","BackgroundCycleTime":"background_cycles",
                 "BytesRead":"bytes_read","BytesWritten":"bytes_written"}),
            "network_usage": _agg(db, T_NET, idmap, hours,
                {"BytesSent":"bytes_sent","BytesRecvd":"bytes_recvd"}),
            "energy_usage": _agg(db, T_ENERGY, idmap, hours, {}),  # fields confirmed in spike; may stay battery-state only
            "tables": [t.name for t in db.tables()],
        }

def _cached(hours):
    now = time.time()
    if _CACHE["data"] is None or now - _CACHE["ts"] > _TTL:
        copy = _copy_locked()
        _CACHE.update(ts=now, data=_parse(copy, hours), copy=copy)
    return _CACHE["data"]

def app_usage(hours=24, top_n=20): return _cached(hours)["app_usage"][:top_n]
def network_usage(hours=24, top_n=20): return _cached(hours)["network_usage"][:top_n]
def energy_usage(hours=24, top_n=20): return _cached(hours)["energy_usage"][:top_n]

def health():
    h = {"srudb_path": SRUDB, "is_admin": is_admin(), "parser_ok": False,
         "cache_age_s": round(time.time()-_CACHE["ts"]) if _CACHE["data"] else None}
    try:
        st = os.stat(SRUDB); h["size_mb"] = round(st.st_size/1048576,1)
        h["last_modified"] = dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
    except Exception as e: h["error"] = f"stat: {e}"
    if not h["is_admin"]:
        h["error"] = "not elevated: SRUM read requires admin (start the server elevated)"; return h
    try:
        data = _cached(24); h["tables_found"] = data["tables"]
        h["row_counts"] = {"app_usage": len(data["app_usage"]), "network_usage": len(data["network_usage"]),
                           "energy_usage": len(data["energy_usage"])}
        h["parser_ok"] = True
    except Exception as e: h["error"] = f"parse: {e}"
    return h
```

- [ ] **Step 4: Create the test fixture, then run tests**

Run (elevated, one-time): copy the spike's DB copy to the fixtures dir —
`mkdir HarnessAgent/mcp/windows_srum/tests/fixtures 2>$null; Copy-Item $env:TEMP\SRUDB_spike.dat HarnessAgent/mcp/windows_srum/tests/fixtures/SRUDB_copy.dat`
Then: `cd HarnessAgent/mcp/windows_srum && python -m pytest tests/test_srum_reader.py -v`
Expected: PASS (or `skipped` only if fixture truly absent). Fix `_cell`/column names per `SCHEMA.md` if assertions fail.

- [ ] **Step 5: Commit** (fixture is gitignored)
```bash
git add HarnessAgent/mcp/windows_srum/srum_reader.py HarnessAgent/mcp/windows_srum/tests/test_srum_reader.py
git commit -m "feat(srum-mcp): SRUM reader (esentutl copy + dissect.esedb parse + cache)"
```

---

### Task 4: `srum_mcp_server.py` — FastMCP server

**Files:**
- Create: `HarnessAgent/mcp/windows_srum/srum_mcp_server.py`
- Test: `HarnessAgent/mcp/windows_srum/tests/test_server_smoke.py`

**Interfaces:**
- Consumes: `live_metrics.snapshot/top_processes`, `srum_reader.health/app_usage/network_usage/energy_usage`.
- Produces: an MCP server with tools `live_snapshot, top_processes, srum_app_usage, srum_network_usage, srum_energy_usage, srum_health` on `http://127.0.0.1:8777/mcp`.

- [ ] **Step 1: Write the failing smoke test** (imports the FastMCP app, asserts tools registered)
```python
# tests/test_server_smoke.py
import srum_mcp_server as s

def test_tools_registered():
    # FastMCP stores tools in its tool manager; assert our names exist.
    names = set(s.list_tool_names())
    assert {"live_snapshot","top_processes","srum_app_usage",
            "srum_network_usage","srum_energy_usage","srum_health"} <= names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd HarnessAgent/mcp/windows_srum && python -m pytest tests/test_server_smoke.py -v`
Expected: FAIL (`ModuleNotFoundError: srum_mcp_server`).

- [ ] **Step 3: Implement `srum_mcp_server.py`**
```python
"""Windows SRUM + live metrics MCP server (FastMCP, streamable HTTP, 127.0.0.1:8777)."""
from mcp.server.fastmcp import FastMCP
import live_metrics, srum_reader

mcp = FastMCP("srum", host="127.0.0.1", port=8777)

@mcp.tool()
def live_snapshot() -> dict:
    """Current CPU/memory/disk/network/power snapshot + top processes (real-time)."""
    return live_metrics.snapshot()

@mcp.tool()
def top_processes(by: str = "cpu", n: int = 10) -> list:
    """Top N processes by 'cpu' or 'memory' (real-time)."""
    return live_metrics.top_processes(by=by, n=n)

@mcp.tool()
def srum_app_usage(hours: int = 24, top_n: int = 20) -> list:
    """Historical per-app CPU cycle time + bytes read/written from SRUM (needs admin)."""
    return srum_reader.app_usage(hours=hours, top_n=top_n)

@mcp.tool()
def srum_network_usage(hours: int = 24, top_n: int = 20) -> list:
    """Historical per-app network bytes sent/received from SRUM (needs admin)."""
    return srum_reader.network_usage(hours=hours, top_n=top_n)

@mcp.tool()
def srum_energy_usage(hours: int = 24, top_n: int = 20) -> list:
    """Historical per-app energy/power usage from SRUM, best-effort per local schema (needs admin)."""
    return srum_reader.energy_usage(hours=hours, top_n=top_n)

@mcp.tool()
def srum_health() -> dict:
    """SRUM DB info, admin status, tables found, parser status."""
    return srum_reader.health()

def list_tool_names():
    # Test helper: FastMCP exposes registered tools via the tool manager.
    import asyncio
    return [t.name for t in asyncio.run(mcp.list_tools())]

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd HarnessAgent/mcp/windows_srum && python -m pytest tests/test_server_smoke.py -v`
Expected: PASS. (If `list_tools()` API differs in the installed `mcp` version, adjust `list_tool_names()` to the SDK's accessor — confirm with `python -c "from mcp.server.fastmcp import FastMCP; help(FastMCP)"`.)

- [ ] **Step 5: Manual HTTP run check**

Run (elevated): `python HarnessAgent/mcp/windows_srum/srum_mcp_server.py` then in another shell:
`curl -s -X POST http://127.0.0.1:8777/mcp -H "Accept: application/json, text/event-stream" -H "Content-Type: application/json" -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}"`
Expected: a JSON/SSE response listing the 6 tools (a 400 to a *malformed* request is fine; the point is the server responds on `/mcp`). Stop the server (Ctrl-C).

- [ ] **Step 6: Commit**
```bash
git add HarnessAgent/mcp/windows_srum/srum_mcp_server.py HarnessAgent/mcp/windows_srum/tests/test_server_smoke.py
git commit -m "feat(srum-mcp): FastMCP server exposing live + SRUM tools"
```

---

### Task 5: Elevated launcher + scheduled task

**Files:**
- Create: `HarnessAgent/mcp/windows_srum/start_srum_mcp.ps1`
- Create: `HarnessAgent/mcp/windows_srum/install_task.ps1`
- Create: `HarnessAgent/mcp/windows_srum/uninstall_task.ps1`

**Interfaces:**
- Produces: a way to run the server elevated on demand and at logon.

- [ ] **Step 1: Write `start_srum_mcp.ps1`** (admin self-check + launch)
```powershell
# Starts the SRUM MCP server elevated. Run as Administrator.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$admin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) {
  Write-Host "[X] Must run elevated. Right-click PowerShell -> Run as Administrator, then re-run this script." -ForegroundColor Red
  exit 1
}
$py = (Get-Command python).Source
Write-Host "[*] Starting SRUM MCP on http://127.0.0.1:8777/mcp ..." -ForegroundColor Cyan
& $py (Join-Path $here "srum_mcp_server.py")
```

- [ ] **Step 2: Write `install_task.ps1`** (scheduled task, highest privileges, at logon)
```powershell
# Registers a Scheduled Task that runs the SRUM MCP server elevated at logon. Run as Administrator.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = (Get-Command python).Source
$server = Join-Path $here "srum_mcp_server.py"
$action = New-ScheduledTaskAction -Execute $py -Argument "`"$server`"" -WorkingDirectory $here
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -RunLevel Highest -LogonType Interactive
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "SRUM-MCP" -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force
Write-Host "[OK] Registered scheduled task 'SRUM-MCP' (elevated, at logon). Start now: Start-ScheduledTask -TaskName SRUM-MCP" -ForegroundColor Green
```

- [ ] **Step 3: Write `uninstall_task.ps1`**
```powershell
Unregister-ScheduledTask -TaskName "SRUM-MCP" -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "[OK] Removed scheduled task 'SRUM-MCP'." -ForegroundColor Green
```

- [ ] **Step 4: Verify launcher non-admin guard**

Run (NON-elevated): `powershell -ExecutionPolicy Bypass -File HarnessAgent/mcp/windows_srum/start_srum_mcp.ps1`
Expected: prints the red "Must run elevated" message and exits 1 (does not start).

- [ ] **Step 5: Commit**
```bash
git add HarnessAgent/mcp/windows_srum/start_srum_mcp.ps1 HarnessAgent/mcp/windows_srum/install_task.ps1 HarnessAgent/mcp/windows_srum/uninstall_task.ps1
git commit -m "feat(srum-mcp): elevated launcher + scheduled-task persistence"
```

---

### Task 6: Wire into goose + README + end-to-end verification

**Files:**
- Modify: `%APPDATA%\Block\goose\config\config.yaml` (live config — add `srum` extension)
- Modify: `HarnessAgent/config/windows_config.yaml` (template — add `srum` extension)
- Create: `HarnessAgent/mcp/windows_srum/README.md`

**Interfaces:**
- Consumes: the running server from Tasks 4–5.

- [ ] **Step 1: Add the `srum` extension to the Windows template** `config/windows_config.yaml` under `extensions:`
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

- [ ] **Step 2: Sync to the live config**

Run: `Copy-Item HarnessAgent/config/windows_config.yaml "$env:APPDATA\Block\goose\config\config.yaml" -Force`
(Then refresh the backup: `Copy-Item "$env:APPDATA\Block\goose\config\config.yaml" "$env:APPDATA\Block\goose\config\config.yaml.bak" -Force`)

- [ ] **Step 3: Write `README.md`** (setup, run, goose snippet, health-check, troubleshooting)
```markdown
# Windows SRUM MCP
Elevated loopback MCP server giving goose live (CPU/mem/net/power) + SRUM historical per-app usage.

## Install
1. `python -m pip install -r requirements.txt`
2. (one-time) `.\install_task.ps1` as Administrator  — auto-starts the server elevated at logon
   OR run on demand (elevated): `.\start_srum_mcp.ps1`
3. Add the `srum` extension to the goose live config (see config/windows_config.yaml).

## Verify
- `srum_health` via goose: `GOOSE_MODE=auto goose run --no-session -t "call srum_health and report it"`
- Live: `... -t "call live_snapshot and summarize CPU, memory, network, power"`

## Notes
- Server MUST be elevated for SRUM (live tools work either way). Goose stays user-mode (talks over loopback HTTP).
- Goose 1.39 uses streamable_http (/mcp), NOT sse.
- SRUM is historical (~hourly). Desktops have no live wattage sensor; per-app energy comes from SRUM.
```

- [ ] **Step 4: End-to-end through goose** (server running elevated from Task 5)

Run: `cd HarnessAgent; $env:GOOSE_MODE="auto"; goose run --no-session --max-turns 4 -t "Call srum_health, then live_snapshot. Report admin status, the SRUM tables found, and current CPU% + memory% + battery."`
Expected: goose shows `▸ srum_health srum` and `▸ live_snapshot srum` tool blocks and reports real values; `srum_health.parser_ok = true`, `is_admin = true`.

- [ ] **Step 5: Commit**
```bash
git add HarnessAgent/config/windows_config.yaml HarnessAgent/mcp/windows_srum/README.md
git commit -m "feat(srum-mcp): wire srum extension into goose config + README"
```

---

## Self-Review
- **Spec coverage:** live tools (Task 2) ✓; SRUM app/network/energy + health (Task 3) ✓; FastMCP streamable_http server on 8777 (Task 4) ✓; elevated launcher + scheduled task (Task 5) ✓; goose wiring + README + e2e (Task 6) ✓; schema-discovery spike (Task 1) ✓; graceful non-admin degradation (`srum_reader.health`/`_cached`) ✓; loopback + read-only + SSE caveat (Global Constraints) ✓; caching (Task 3 `_TTL`) ✓.
- **Placeholders:** none — every code step has full code. The two intentional spike-confirmed spots (`_cell` access API, `TimeStamp` format, energy columns) have working defaults + explicit "adjust per SCHEMA.md" instructions.
- **Type consistency:** tool names match between Task 4 server and Task 6 config (`srum`); reader function names (`app_usage/network_usage/energy_usage/health`) consistent across Tasks 3–4; `snapshot/top_processes` consistent across Tasks 2 & 4.
- **Known residual risk:** exact SRUM column names + energy schema vary by Windows build → Task 1 resolves before Task 3 code is finalized; defaults use the documented standard columns.
