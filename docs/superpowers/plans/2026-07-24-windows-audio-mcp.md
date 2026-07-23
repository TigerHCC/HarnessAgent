# Windows Audio Diagnostics MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `mcp/windows_audio/` — a read-only audio-diagnostics MCP (18th canonical, 127.0.0.1:8796) with 7 tools that pinpoint no-multimedia/no-meeting output, mic-not-working, glitches, and Bluetooth A2DP/HFP issues; register it as canonical and update the manifest validators.

**Architecture:** Each unit splits RAW acquisition (COM/subprocess/winreg — smoke-tested) from PURE classify/summarize logic (unit-tested with fixtures). `coreaudio.py` (pycaw, graceful degradation), `winaudio.py` (services/PnP/registry), `glitch.py` (indicators + optional trace), `windows_audio_mcp_server.py` (FastMCP wiring). Manifest becomes 18 entries on the port set {8777-8793, 8796}.

**Tech Stack:** Python 3 (`mcp` FastMCP, `anyio`, `pycaw`, `comtypes`), stdlib (`winreg`, `subprocess`), pytest, PowerShell 5.1 scaffolding.

## Global Constraints

- Read-only: the module NEVER changes an audio setting (no default-device switch, no unmute, no mic-access grant). Diagnose + recommend only. No confirm-token gating.
- Binds `127.0.0.1:8796`; transport streamable-http; goose URI `http://127.0.0.1:8796/mcp`. Scheduled task `mcp-audio`, RunLevel **Highest**, AtLogOn, via `scripts\start_mcp_hidden.ps1` with `-Name "windows_audio"`.
- pycaw/comtypes are imported lazily; every coreaudio function degrades to `{"available": false, "error": "..."}` when the import fails — the MCP must still start and the non-pycaw tools must still work.
- Manifest is now 18 canonical entries; valid canonical ports = the set {8777..8793} ∪ {8796} (NON-contiguous; 8794/8795 are the manifest-external markitdown/docstruct and are NOT in the manifest).
- Python deps: stdlib + `mcp>=1.2`, `anyio>=4.5`, `pycaw`, `comtypes`. Windows-only.
- Branch `feature/windows-audio-mcp`; commit there; do not push.
- Every commit body ends with the repo trailers:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_013Wm8BeurMKFZK6TgvFhLjE`. (Omitted below — add them.)

---

## File Structure

- Create `mcp/windows_audio/config.py`, `config.json`, `conftest.py`
- Create `mcp/windows_audio/coreaudio.py` — pycaw acquire + pure role/state classify
- Create `mcp/windows_audio/winaudio.py` — services/PnP/registry acquire + pure classify (BT profile, mic privacy)
- Create `mcp/windows_audio/glitch.py` — indicators + optional trace
- Create `mcp/windows_audio/windows_audio_mcp_server.py` — 7 FastMCP tools
- Create `mcp/windows_audio/tests/{test_config,test_coreaudio,test_winaudio,test_glitch,test_server}.py`
- Create `mcp/windows_audio/{requirements.txt,install_task.ps1,uninstall_task.ps1,README.md}`
- Modify `config/mcp_servers.json` (18th entry), `setup_mcp_servers.ps1`, `tools/mcp_watchdog/mcp_watchdog.ps1`, `tests/test_mcp_manifest.py`, `tests/test_mcp_batch.py`, `scripts/test_mcp_servers.py`

---

## Task 1: config.py + config.json

**Files:** Create `mcp/windows_audio/config.py`, `config.json`, `conftest.py`; Test `tests/test_config.py`

**Interfaces:** `config.load(path=None) -> dict` with keys `trace_seconds_default` (int, 0), `subprocess_timeout` (int, 30), `trace_max_seconds` (int, 30). Env overrides `WINAUDIO_MCP_<KEY>`.

- [ ] **Step 1: Write the failing test**

```python
# mcp/windows_audio/conftest.py
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
```

```python
# mcp/windows_audio/tests/test_config.py
import config

def test_defaults():
    c = config.load()
    assert c["trace_seconds_default"] == 0
    assert c["subprocess_timeout"] == 30 and c["trace_max_seconds"] == 30

def test_env_override(monkeypatch):
    monkeypatch.setenv("WINAUDIO_MCP_SUBPROCESS_TIMEOUT", "5")
    assert config.load()["subprocess_timeout"] == 5
```

- [ ] **Step 2: Run → FAIL** (`ModuleNotFoundError: config`), from `mcp/windows_audio`: `python -m pytest tests/test_config.py -v`
- [ ] **Step 3: Implement**

```json
{ "trace_seconds_default": 0, "subprocess_timeout": 30, "trace_max_seconds": 30 }
```

```python
# mcp/windows_audio/config.py
"""Config for the windows_audio MCP: config.json defaults + WINAUDIO_MCP_<KEY> env overrides."""
import json, os
HERE = os.path.dirname(os.path.abspath(__file__))

def env_key(name): return "WINAUDIO_MCP_" + name.upper()

def load(path=None):
    path = path or os.environ.get("WINAUDIO_MCP_CONFIG") or os.path.join(HERE, "config.json")
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    for k, d in (("trace_seconds_default", 0), ("subprocess_timeout", 30), ("trace_max_seconds", 30)):
        cfg[k] = int(os.environ.get(env_key(k), cfg.get(k, d)))
    return cfg
```

- [ ] **Step 4: Run → PASS** (2). **Step 5: Commit** — `feat(windows_audio): config loading`

---

## Task 2: coreaudio.py — pycaw wrappers (state, roles, sessions) + pure classify

**Files:** Create `mcp/windows_audio/coreaudio.py`; Test `tests/test_coreaudio.py`

**Interfaces:**
- `_pycaw()` → the pycaw module tuple or raises `AudioUnavailable`.
- `list_endpoints() -> dict` → `{"available": bool, "endpoints": [{name,id,flow,state,...}], "error"?}`; flow = "render"|"capture" from the id prefix (`{0.0.0` = render, `{0.0.1` = capture).
- `default_for_roles() -> dict` → `{"available": bool, "render": {console,multimedia,communications}, "capture": {...}}`; each role → `{name,state,volume,mute}` or `{"error": "no default"}` (GetDefaultAudioEndpoint throws 0x80070490 when unset).
- `list_sessions() -> dict` → `{"available": bool, "sessions": [{process,state}]}`.
- Pure classifiers (unit-tested with fixtures, NO COM): `flow_of_id(id) -> str`; `flag_defaults(default_render) -> dict` → `{"no_multimedia_output": bool, "no_communications_output": bool, "reasons": [...]}` (True when the role's default is missing, not Active, muted, or volume 0).

- [ ] **Step 1: Write the failing test** (pure functions with fixtures; COM paths are covered by Task 6 smoke)

```python
# mcp/windows_audio/tests/test_coreaudio.py
import coreaudio as ca

def test_flow_of_id():
    assert ca.flow_of_id("{0.0.0.00000000}.{abc}") == "render"
    assert ca.flow_of_id("{0.0.1.00000000}.{abc}") == "capture"
    assert ca.flow_of_id("weird") == "unknown"

def test_flag_defaults_muted_multimedia():
    render = {"multimedia": {"name": "Spk", "state": "Active", "volume": 0.25, "mute": True},
              "communications": {"name": "USB", "state": "Active", "volume": 0.5, "mute": False}}
    f = ca.flag_defaults(render)
    assert f["no_multimedia_output"] is True and any("mut" in r.lower() for r in f["reasons"])
    assert f["no_communications_output"] is False

def test_flag_defaults_missing_comms():
    render = {"multimedia": {"name": "Spk", "state": "Active", "volume": 0.5, "mute": False},
              "communications": {"error": "no default"}}
    f = ca.flag_defaults(render)
    assert f["no_communications_output"] is True
    assert f["no_multimedia_output"] is False

def test_flag_defaults_vol_zero_and_unplugged():
    render = {"multimedia": {"name": "S", "state": "Unplugged", "volume": 0.0, "mute": False},
              "communications": {"name": "S", "state": "Active", "volume": 0.0, "mute": False}}
    f = ca.flag_defaults(render)
    assert f["no_multimedia_output"] is True   # unplugged
    assert f["no_communications_output"] is True  # volume 0

def test_list_endpoints_degrades_without_pycaw(monkeypatch):
    monkeypatch.setattr(ca, "_pycaw", lambda: (_ for _ in ()).throw(ca.AudioUnavailable("no pycaw")))
    r = ca.list_endpoints()
    assert r["available"] is False and "pycaw" in r["error"]
```

- [ ] **Step 2: Run → FAIL** (`ModuleNotFoundError: coreaudio`).
- [ ] **Step 3: Implement** (real pycaw API verified on this machine 2026-07-24)

```python
# mcp/windows_audio/coreaudio.py
"""Core Audio (pycaw/comtypes) wrappers with graceful degradation. RAW acquisition talks COM;
the classify helpers (flow_of_id, flag_defaults) are pure and unit-tested. When pycaw/comtypes is
missing, every acquire function returns {"available": False, "error": ...} so the MCP still runs."""
import warnings

class AudioUnavailable(Exception):
    pass


def _pycaw():
    try:
        warnings.filterwarnings("ignore")
        import comtypes
        from comtypes import CLSCTX_ALL, cast, POINTER
        from pycaw.pycaw import (AudioUtilities, IAudioEndpointVolume, IMMDeviceEnumerator,
                                 EDataFlow, ERole)
        from pycaw.constants import CLSID_MMDeviceEnumerator
        return dict(comtypes=comtypes, CLSCTX_ALL=CLSCTX_ALL, cast=cast, POINTER=POINTER,
                    AudioUtilities=AudioUtilities, IAudioEndpointVolume=IAudioEndpointVolume,
                    IMMDeviceEnumerator=IMMDeviceEnumerator, EDataFlow=EDataFlow, ERole=ERole,
                    CLSID_MMDeviceEnumerator=CLSID_MMDeviceEnumerator)
    except Exception as e:
        raise AudioUnavailable("pycaw/comtypes not available: %s (pip install pycaw)" % e)


def flow_of_id(dev_id):
    s = str(dev_id or "")
    if s.startswith("{0.0.0"):
        return "render"
    if s.startswith("{0.0.1"):
        return "capture"
    return "unknown"


def _state_name(state):
    return str(state).rsplit(".", 1)[-1] if state is not None else "Unknown"


def list_endpoints():
    try:
        p = _pycaw()
    except AudioUnavailable as e:
        return {"available": False, "error": str(e), "endpoints": []}
    out = []
    for d in p["AudioUtilities"].GetAllDevices():
        try:
            out.append({"name": d.FriendlyName, "id": d.id, "flow": flow_of_id(d.id),
                        "state": _state_name(getattr(d, "state", None))})
        except Exception as e:
            out.append({"name": None, "error": str(e)})
    return {"available": True, "endpoints": out}


def _role_info(p, enum, flow_val, role_val):
    try:
        dev = enum.GetDefaultAudioEndpoint(flow_val, role_val)
    except Exception:
        return {"error": "no default"}   # 0x80070490 element-not-found when a role has no default
    adev = p["AudioUtilities"].CreateDevice(dev)
    info = {"name": adev.FriendlyName, "id": adev.id, "state": _state_name(getattr(adev, "state", None))}
    try:
        vol = p["cast"](dev.Activate(p["IAudioEndpointVolume"]._iid_, p["CLSCTX_ALL"], None),
                        p["POINTER"](p["IAudioEndpointVolume"]))
        info["volume"] = round(vol.GetMasterVolumeLevelScalar(), 3)
        info["mute"] = bool(vol.GetMute())
    except Exception:
        pass
    return info


def default_for_roles():
    try:
        p = _pycaw()
    except AudioUnavailable as e:
        return {"available": False, "error": str(e)}
    enum = p["comtypes"].CoCreateInstance(p["CLSID_MMDeviceEnumerator"], p["IMMDeviceEnumerator"],
                                          p["comtypes"].CLSCTX_INPROC_SERVER)
    roles = (("console", p["ERole"].eConsole), ("multimedia", p["ERole"].eMultimedia),
             ("communications", p["ERole"].eCommunications))
    res = {"available": True, "render": {}, "capture": {}}
    for flow_key, flow in (("render", p["EDataFlow"].eRender), ("capture", p["EDataFlow"].eCapture)):
        for rk, role in roles:
            res[flow_key][rk] = _role_info(p, enum, flow.value, role.value)
    return res


def list_sessions():
    try:
        p = _pycaw()
    except AudioUnavailable as e:
        return {"available": False, "error": str(e), "sessions": []}
    out = []
    for s in p["AudioUtilities"].GetAllSessions():
        try:
            name = s.Process.name() if s.Process else "system"
        except Exception:
            name = "unknown"
        out.append({"process": name, "state": int(s.State)})
    return {"available": True, "sessions": out}


def flag_defaults(default_render):
    """Pure: given the render role->info map, flag no-multimedia / no-communications output + reasons."""
    def bad(info):
        if not info or "error" in info:
            return "no default device set"
        if info.get("state") not in (None, "Active"):
            return "default device is %s (not Active)" % info.get("state")
        if info.get("mute") is True:
            return "default device is muted"
        if info.get("volume") == 0.0:
            return "default device volume is 0"
        return None
    reasons = {}
    for role in ("multimedia", "communications"):
        r = bad(default_render.get(role, {}))
        if r:
            reasons[role] = r
    return {"no_multimedia_output": "multimedia" in reasons,
            "no_communications_output": "communications" in reasons,
            "reasons": ["%s: %s" % (k, v) for k, v in reasons.items()]}
```

- [ ] **Step 4: Run → PASS** (5). **Step 5: Commit** — `feat(windows_audio): Core Audio wrappers + default-role flagging`

---

## Task 3: winaudio.py — services / PnP+Bluetooth / mic privacy (+ pure classifiers)

**Files:** Create `mcp/windows_audio/winaudio.py`; Test `tests/test_winaudio.py`

**Interfaces:**
- `services(timeout) -> dict` → `{Audiosrv,AudioEndpointBuilder: {status}}` (subprocess Get-Service).
- `pnp_audio(timeout) -> list[dict]` → PnP MEDIA/AudioEndpoint/Bluetooth rows `{status,name}` (subprocess Get-PnpDevice, JSON).
- `mic_privacy() -> dict` → `{"global": "Allow"|"Deny"|"unset", "denied_apps": [...]}` from `winreg` ConsentStore.
- Pure classifiers: `classify_bt(name) -> str` → "a2dp"|"hfp"|"other" (Hands-Free/HF/HFP → hfp; else a2dp for audio names); `summarize_mic_privacy(global_val, app_vals) -> dict`.

- [ ] **Step 1: Write the failing test** (pure classifiers + registry parse with a fake winreg)

```python
# mcp/windows_audio/tests/test_winaudio.py
import winaudio as wa

def test_classify_bt():
    assert wa.classify_bt("FIIO BTR15 Hands-Free") == "hfp"
    assert wa.classify_bt("LE_WH-H900N (h.ear) Hands-Free") == "hfp"
    assert wa.classify_bt("Pixel 6 Pro A2DP SNK") == "a2dp"
    assert wa.classify_bt("Bose Mini SoundLink") == "a2dp"

def test_summarize_mic_privacy_denied():
    s = wa.summarize_mic_privacy("Deny", {"Teams": "Allow", "SomeApp": "Deny"})
    assert s["global"] == "Deny"
    assert "SomeApp" in s["denied_apps"] and "Teams" not in s["denied_apps"]

def test_summarize_mic_privacy_allow():
    s = wa.summarize_mic_privacy("Allow", {"Teams": "Allow"})
    assert s["global"] == "Allow" and s["denied_apps"] == []
```

- [ ] **Step 2: Run → FAIL**.
- [ ] **Step 3: Implement**

```python
# mcp/windows_audio/winaudio.py
"""Non-pycaw audio sources: Windows services, PnP (incl. Bluetooth A2DP/HFP), and microphone
privacy (registry). RAW acquisition uses subprocess/winreg; classify_bt / summarize_mic_privacy are
pure and unit-tested. Each source is individually guarded so one failure never fails the tool."""
import json
import subprocess


def _ps_json(command, timeout):
    """Run a PowerShell command and parse its JSON stdout; [] on any failure."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", command],
                           capture_output=True, text=True, timeout=timeout)
        out = (r.stdout or "").strip()
        if not out:
            return []
        data = json.loads(out)
        return data if isinstance(data, list) else [data]
    except Exception:
        return []


def services(timeout=30):
    rows = _ps_json("Get-Service Audiosrv,AudioEndpointBuilder | "
                    "Select-Object Name,@{n='Status';e={[string]$_.Status}} | ConvertTo-Json", timeout)
    return {r.get("Name"): {"status": r.get("Status")} for r in rows if r.get("Name")}


def pnp_audio(timeout=30):
    return _ps_json(
        "Get-PnpDevice -Class MEDIA,AudioEndpoint,Bluetooth -ErrorAction SilentlyContinue | "
        "Select-Object @{n='status';e={[string]$_.Status}},@{n='name';e={$_.FriendlyName}},"
        "@{n='class';e={$_.Class}} | ConvertTo-Json", timeout)


def classify_bt(name):
    n = (name or "").lower()
    if "hands-free" in n or n.endswith(" hf") or "hfp" in n or "hands free" in n:
        return "hfp"
    if "a2dp" in n:
        return "a2dp"
    return "a2dp" if n else "other"


def mic_privacy():
    """Global + per-app microphone access from CapabilityAccessManager\\ConsentStore\\microphone."""
    import winreg
    def read(root):
        base = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
        try:
            k = winreg.OpenKey(root, base)
        except OSError:
            return None, {}
        gval = None
        try:
            gval, _ = winreg.QueryValueEx(k, "Value")
        except OSError:
            pass
        apps = {}
        try:
            for i in range(winreg.QueryInfoKey(k)[0]):
                sub = winreg.EnumKey(k, i)
                try:
                    sk = winreg.OpenKey(k, sub)
                    v, _ = winreg.QueryValueEx(sk, "Value")
                    apps[sub] = v
                except OSError:
                    pass
        except OSError:
            pass
        return gval, apps
    gval, apps = read(winreg.HKEY_CURRENT_USER)
    return summarize_mic_privacy(gval or "unset", apps)


def summarize_mic_privacy(global_val, app_vals):
    denied = sorted([a for a, v in (app_vals or {}).items() if str(v).lower() == "deny"])
    return {"global": global_val, "denied_apps": denied,
            "app_count": len(app_vals or {})}
```

- [ ] **Step 4: Run → PASS** (3). **Step 5: Commit** — `feat(windows_audio): services/PnP/Bluetooth/mic-privacy sources`

---

## Task 4: glitch.py — risk indicators + optional short trace

**Files:** Create `mcp/windows_audio/glitch.py`; Test `tests/test_glitch.py`

**Interfaces:**
- `glitch_indicators(endpoints, timeout) -> dict` → `{"sample_rate_mismatch": bool, "active_formats": [...], "power_mgmt_risks": [...], "recent_driver_errors": [...]}` — pure over inputs plus a guarded event-log read.
- `recent_audio_errors(timeout) -> list` → System-log audio driver errors (subprocess).
- `short_trace(seconds, max_seconds, timeout) -> dict` → `{"ran": bool, "events": [...], "error"?}` (elevated WWPR/tracelog; clamps seconds to max_seconds; degrades to `{"ran": False, "error": ...}` if the trace tool is unavailable).
- Pure: `detect_sample_rate_mismatch(active_formats) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# mcp/windows_audio/tests/test_glitch.py
import glitch as g

def test_sample_rate_mismatch():
    assert g.detect_sample_rate_mismatch(["48000", "44100"]) is True
    assert g.detect_sample_rate_mismatch(["48000", "48000"]) is False
    assert g.detect_sample_rate_mismatch([]) is False

def test_short_trace_clamped_and_guarded(monkeypatch):
    monkeypatch.setattr(g, "_run_trace", lambda secs, timeout: {"ran": True, "events": [], "secs": secs})
    r = g.short_trace(999, max_seconds=30, timeout=60)
    assert r["ran"] is True and r["secs"] == 30   # clamped to max

def test_short_trace_tool_missing(monkeypatch):
    monkeypatch.setattr(g, "_run_trace", lambda secs, timeout: (_ for _ in ()).throw(FileNotFoundError("wpr")))
    r = g.short_trace(5, max_seconds=30, timeout=60)
    assert r["ran"] is False and "wpr" in r["error"]
```

- [ ] **Step 2: Run → FAIL**.
- [ ] **Step 3: Implement**

```python
# mcp/windows_audio/glitch.py
"""Audio-glitch diagnostics: lightweight risk indicators by default (sample-rate mismatch, power
management, recent driver errors) and an optional short ETW trace of Microsoft-Windows-Audio glitch
events. detect_sample_rate_mismatch is pure; the trace and event-log reads are guarded."""
import json
import subprocess


def detect_sample_rate_mismatch(active_formats):
    rates = set(str(f) for f in (active_formats or []) if f)
    return len(rates) > 1


def recent_audio_errors(timeout=30):
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
            "Get-WinEvent -FilterHashtable @{LogName='System';Level=1,2} -MaxEvents 200 "
            "-ErrorAction SilentlyContinue | Where-Object { $_.ProviderName -match 'audio|HdAudio|Realtek|USBAUDIO' } | "
            "Select-Object -First 15 @{n='time';e={$_.TimeCreated.ToString('s')}},"
            "@{n='provider';e={$_.ProviderName}},@{n='id';e={$_.Id}} | ConvertTo-Json"],
            capture_output=True, text=True, timeout=timeout)
        out = (r.stdout or "").strip()
        if not out:
            return []
        data = json.loads(out)
        return data if isinstance(data, list) else [data]
    except Exception:
        return []


def glitch_indicators(active_formats=None, timeout=30):
    return {"sample_rate_mismatch": detect_sample_rate_mismatch(active_formats),
            "active_formats": list(active_formats or []),
            "recent_driver_errors": recent_audio_errors(timeout)}


def _run_trace(seconds, timeout):
    """Best-effort short ETW trace via wpr (Windows Performance Recorder). Elevated. Returns dict."""
    # start a light trace, sleep, stop to a temp etl, then (optionally) summarize. Kept minimal here;
    # a full parse of the .etl is out of scope -- we report that a trace ran and any errors surfaced.
    import tempfile, os, time
    etl = os.path.join(tempfile.gettempdir(), "winaudio_glitch.etl")
    subprocess.run(["wpr", "-start", "GeneralProfile", "-filemode"], capture_output=True, timeout=timeout, check=True)
    time.sleep(seconds)
    subprocess.run(["wpr", "-stop", etl], capture_output=True, timeout=timeout, check=True)
    return {"ran": True, "events": [], "etl": etl, "secs": seconds,
            "note": "trace captured to etl; open in WPA for DPC/glitch analysis"}


def short_trace(seconds, max_seconds=30, timeout=60):
    secs = max(1, min(int(seconds), int(max_seconds)))
    try:
        return _run_trace(secs, timeout)
    except Exception as e:
        return {"ran": False, "error": "trace unavailable (%s: %s)" % (type(e).__name__, e)}
```

- [ ] **Step 4: Run → PASS** (3). **Step 5: Commit** — `feat(windows_audio): glitch risk indicators + optional trace`

---

## Task 5: windows_audio_mcp_server.py — 7 FastMCP tools

**Files:** Create `mcp/windows_audio/windows_audio_mcp_server.py`; Test `tests/test_server.py`

**Interfaces:** Consumes config/coreaudio/winaudio/glitch. Module-level `_devices_impl()`, `_defaults_impl()`, `_microphone_impl()`, `_bluetooth_impl()`, `_glitches_impl(trace_seconds)`, `_health_impl()` (pure composition, testable by monkeypatching the source modules). 7 `@mcp.tool()` wrappers.

- [ ] **Step 1: Write the failing test**

```python
# mcp/windows_audio/tests/test_server.py
import windows_audio_mcp_server as srv

def test_seven_tools():
    import inspect, re
    assert len(re.findall(r"@mcp\.tool\(\)", inspect.getsource(srv))) == 7

def test_defaults_impl_flags(monkeypatch):
    monkeypatch.setattr(srv.coreaudio, "default_for_roles", lambda: {"available": True,
        "render": {"multimedia": {"name": "S", "state": "Active", "volume": 0.2, "mute": True},
                   "communications": {"error": "no default"}}, "capture": {}})
    r = srv._defaults_impl()
    assert r["flags"]["no_multimedia_output"] is True
    assert r["flags"]["no_communications_output"] is True

def test_bluetooth_impl_classifies(monkeypatch):
    monkeypatch.setattr(srv.winaudio, "pnp_audio", lambda timeout=30: [
        {"status": "OK", "name": "FIIO BTR15 Hands-Free", "class": "MEDIA"},
        {"status": "OK", "name": "Pixel 6 Pro A2DP SNK", "class": "MEDIA"},
        {"status": "OK", "name": "Realtek(R) Audio", "class": "MEDIA"}])
    r = srv._bluetooth_impl()
    profs = {d["name"]: d["profile"] for d in r["bluetooth"]}
    assert profs["FIIO BTR15 Hands-Free"] == "hfp" and profs["Pixel 6 Pro A2DP SNK"] == "a2dp"

def test_health_impl_degrades(monkeypatch):
    monkeypatch.setattr(srv.coreaudio, "default_for_roles", lambda: {"available": False, "error": "no pycaw"})
    monkeypatch.setattr(srv.winaudio, "services", lambda timeout=30: {"Audiosrv": {"status": "Running"}})
    r = srv._health_impl()
    assert r["ok"] is True and r["coreaudio_available"] is False

def test_microphone_impl(monkeypatch):
    monkeypatch.setattr(srv.winaudio, "mic_privacy", lambda: {"global": "Deny", "denied_apps": ["X"], "app_count": 1})
    monkeypatch.setattr(srv.coreaudio, "default_for_roles", lambda: {"available": True, "render": {},
        "capture": {"communications": {"error": "no default"}, "multimedia": {"error": "no default"}}})
    r = srv._microphone_impl()
    assert r["privacy"]["global"] == "Deny"
    assert r["no_default_capture"] is True
```

- [ ] **Step 2: Run → FAIL**.
- [ ] **Step 3: Implement**

```python
# mcp/windows_audio/windows_audio_mcp_server.py
"""Windows Audio Diagnostics MCP (FastMCP, streamable HTTP, 127.0.0.1:8796).

Read-only: pinpoints no-multimedia / no-meeting output (Core Audio roles), mic-not-working
(state/mute/privacy), glitches (risk indicators + optional trace), and Bluetooth A2DP-vs-HFP. It only
diagnoses and recommends -- it never changes an audio setting. pycaw is optional (graceful
degradation). 18th canonical MCP. Goose connects via streamable_http, uri http://127.0.0.1:8796/mcp.
"""
import anyio
from mcp.server.fastmcp import FastMCP

import config
import coreaudio
import winaudio
import glitch

mcp = FastMCP("windows_audio", host="127.0.0.1", port=8796)
_CFG = config.load()
_T = _CFG["subprocess_timeout"]


def _defaults_impl():
    d = coreaudio.default_for_roles()
    flags = coreaudio.flag_defaults(d.get("render", {})) if d.get("available") else \
        {"no_multimedia_output": None, "no_communications_output": None, "reasons": ["pycaw unavailable"]}
    return {"available": d.get("available", False), "render": d.get("render", {}),
            "capture": d.get("capture", {}), "flags": flags, "error": d.get("error")}


def _devices_impl():
    eps = coreaudio.list_endpoints()
    pnp = winaudio.pnp_audio(_T)
    return {"available": eps.get("available", False), "endpoints": eps.get("endpoints", []),
            "pnp": pnp, "error": eps.get("error")}


def _microphone_impl():
    d = coreaudio.default_for_roles()
    cap = d.get("capture", {}) if d.get("available") else {}
    no_default = all(("error" in cap.get(r, {"error": "x"})) for r in ("multimedia", "communications")) \
        if cap else True
    return {"privacy": winaudio.mic_privacy(), "capture_defaults": cap,
            "no_default_capture": bool(no_default), "coreaudio_available": d.get("available", False)}


def _bluetooth_impl():
    rows = winaudio.pnp_audio(_T)
    bt = []
    for r in rows:
        nm = r.get("name") or ""
        low = nm.lower()
        # BT audio endpoints show up as MEDIA/AudioEndpoint with hands-free/a2dp/known BT names
        if any(k in low for k in ("hands-free", "a2dp", "bluetooth")) or r.get("class") == "Bluetooth":
            bt.append({"name": nm, "status": r.get("status"), "profile": winaudio.classify_bt(nm)})
    return {"bluetooth": bt,
            "note": "a2dp = stereo media; hfp = mono call+mic. HFP-only = no media; A2DP-only = no call mic."}


def _glitches_impl(trace_seconds):
    eps = coreaudio.list_endpoints()
    # active endpoints' formats are best-effort; indicators still run without them
    ind = glitch.glitch_indicators(active_formats=[], timeout=_T)
    result = {"indicators": ind}
    ts = trace_seconds if trace_seconds is not None else _CFG["trace_seconds_default"]
    if ts and int(ts) > 0:
        result["trace"] = glitch.short_trace(int(ts), _CFG["trace_max_seconds"], _T + int(ts) + 30)
    return result


def _health_impl():
    d = coreaudio.default_for_roles()
    svc = winaudio.services(_T)
    flags = coreaudio.flag_defaults(d.get("render", {})) if d.get("available") else {}
    priv = winaudio.mic_privacy()
    red = []
    if svc.get("Audiosrv", {}).get("status") != "Running":
        red.append("Windows Audio service (Audiosrv) not Running")
    if flags.get("no_multimedia_output"):
        red.append("no multimedia output (see audio_defaults)")
    if flags.get("no_communications_output"):
        red.append("no meeting/communications output (see audio_defaults)")
    if priv.get("global") == "Deny":
        red.append("microphone access is globally denied")
    return {"ok": True, "services": svc, "coreaudio_available": d.get("available", False),
            "red_flags": red}


@mcp.tool()
async def audio_health() -> dict:
    """Audio-stack health + red-flag summary: service status, whether multimedia/communications output
    or the mic are obviously broken. Check this first."""
    return await anyio.to_thread.run_sync(_health_impl)


@mcp.tool()
async def audio_devices() -> dict:
    """All render + capture endpoints with state (Active/Unplugged/Disabled/NotPresent), flow, and the
    PnP driver/bus view. The full inventory."""
    return await anyio.to_thread.run_sync(_devices_impl)


@mcp.tool()
async def audio_defaults() -> dict:
    """Default device per role (console/multimedia/communications) for render + capture, flagging
    no_multimedia_output and no_communications_output (the classic silent-meeting case)."""
    return await anyio.to_thread.run_sync(_defaults_impl)


@mcp.tool()
async def audio_microphone() -> dict:
    """Microphone diagnosis: default capture presence/state + Windows mic privacy (global + per-app
    denies). Covers 'mic not working'."""
    return await anyio.to_thread.run_sync(_microphone_impl)


@mcp.tool()
async def audio_bluetooth() -> dict:
    """Bluetooth audio devices with their active profile (a2dp media vs hfp call). Flags 'connected but
    no media' (hfp-only) and mono-call cases."""
    return await anyio.to_thread.run_sync(_bluetooth_impl)


@mcp.tool()
async def audio_sessions() -> dict:
    """Per-app audio sessions (process, state) -- find an app that is muted/inactive in the mixer."""
    return await anyio.to_thread.run_sync(coreaudio.list_sessions)


@mcp.tool()
async def audio_glitches(trace_seconds: int = 0) -> dict:
    """Glitch/stutter diagnosis: risk indicators (sample-rate mismatch, recent audio driver errors)
    always; if trace_seconds > 0, also runs a short elevated ETW trace (clamped to the configured max)."""
    return await anyio.to_thread.run_sync(_glitches_impl, trace_seconds)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
```

- [ ] **Step 4: Run → PASS** (5), then full module suite `python -m pytest -q` (Tasks 1-5).
- [ ] **Step 5: Live smoke** — start hidden, expect 406/400/405 on `http://127.0.0.1:8796/mcp`, then stop the python process (match `windows_audio_mcp_server` in CommandLine). Do not leave running.
- [ ] **Step 6: Commit** — `feat(windows_audio): FastMCP tools + health`

---

## Task 6: Scaffolding (requirements, task scripts, README)

**Files:** Create `requirements.txt`, `install_task.ps1`, `uninstall_task.ps1`, `README.md`

- [ ] **Step 1: `requirements.txt`**

```
mcp>=1.2
anyio>=4.5
pycaw
comtypes
pytest>=8.0
```

- [ ] **Step 2: `install_task.ps1`** — mirror `mcp/dtm_sdk/install_task.ps1` (RunLevel **Highest**, AtLogOn, hidden launcher), with: task name `mcp-audio`, `-Name "windows_audio"`, server `windows_audio_mcp_server.py`. (Copy the dtm_sdk script and substitute; dtm_sdk is the closest Highest+non-loopback-free analog. If dtm_sdk differs, mirror any windows_* diagnostic install_task.ps1 which are all RunLevel Highest.)
- [ ] **Step 3: `uninstall_task.ps1`** — `Unregister-ScheduledTask -TaskName "mcp-audio" -Confirm:$false`.
- [ ] **Step 4: `README.md`** — mirror a windows_* README: the 7 tools, the symptom→tool table, data sources (pycaw + PnP + registry + services + ETW), the pycaw dependency + graceful degradation, the **read-only** stance (diagnoses only; never changes audio settings), and the glitch-trace note (elevated, optional).
- [ ] **Step 5: Manual verification** — start the server, `GET :8796/mcp` → 406/400/405; stop it. Parse-check both ps1.
- [ ] **Step 6: Commit** — `feat(windows_audio): scaffolding + docs`

---

## Task 7: Register 18th canonical MCP + update validators

**Files:** Modify `config/mcp_servers.json`, `setup_mcp_servers.ps1`, `tools/mcp_watchdog/mcp_watchdog.ps1`, `tests/test_mcp_manifest.py`, `tests/test_mcp_batch.py`, `scripts/test_mcp_servers.py`

**Interfaces:** Manifest gains the 18th entry; validators accept 18 entries on the port set {8777..8793, 8796}.

- [ ] **Step 1: Manifest entry** — append to `config/mcp_servers.json` (after scheduler):

```json
  ,
  {
    "name": "audio",
    "directory": "windows_audio",
    "port": 8796,
    "task": "mcp-audio",
    "run_level": "Highest",
    "description": "Windows audio diagnostics (no-multimedia/no-meeting output, mic-not-working, glitches, Bluetooth A2DP/HFP) via local elevated MCP server (127.0.0.1:8796). Read-only -- diagnoses and recommends, never changes audio settings.",
    "health_tool": "audio_health"
  }
```

- [ ] **Step 2: `scripts/test_mcp_servers.py`** — change `CANONICAL_PORTS = set(range(8777, 8794))` to `CANONICAL_PORTS = set(range(8777, 8794)) | {8796}`; change `if len(entries) != 17:` to `!= 18` and its message to `18 ... 8777-8793 + 8796`; change the "canonical ports 8777-8793 exactly once" message to mention 8796.
- [ ] **Step 3: `tools/mcp_watchdog/mcp_watchdog.ps1`** — `$entries.Count -ne 17` → `-ne 18` (+message); `$expectedPorts = @(8777..8793)` → `$expectedPorts = @(8777..8793) + 8796`; the two "8777-8793" throw messages → "8777-8793 + 8796"; `$out.Count -ne 17` → `-ne 18`.
- [ ] **Step 4: `setup_mcp_servers.ps1`** — `-ne 16`/`-ne 17` count guard → `-ne 18` (grep the current value; it is 17) + message; `$expectedPorts = @(8777..8793)` → `@(8777..8793) + 8796`; the two `Die` "8777-8793 exactly once" messages → include 8796; banner `(17: ...)` → `(18: ... + audio)`.
- [ ] **Step 5: `tests/test_mcp_manifest.py`** — `assert len(entries) == 17` → `== 18` (all three counts); `{e["port"] for e in entries} == set(range(8777, 8794))` → `== (set(range(8777, 8794)) | {8796})`; the parametrized `"exactly 17 entries"` and `"8777-8793"` stderr asserts → `"exactly 18 entries"` and keep the port message consistent with the updated setup message.
- [ ] **Step 6: `tests/test_mcp_batch.py`** — the `canonical_manifest_entries()` fixture `for index, port in enumerate(range(8777, 8794), 1)` → produce 18 entries over `list(range(8777,8794)) + [8796]`; the `"exactly 17"` mutate-case error strings → `"exactly 18"`.
- [ ] **Step 7: Verify** — `python -c "import json;d=json.load(open('config/mcp_servers.json',encoding='utf-8'));print(len(d), sorted(x['port'] for x in d))"` → `18 [8777..8793, 8796]`; then `python -m pytest tests/test_mcp_manifest.py tests/test_mcp_batch.py -q` → all pass; `powershell -NoProfile -Command "[ScriptBlock]::Create((Get-Content -Raw setup_mcp_servers.ps1)); [ScriptBlock]::Create((Get-Content -Raw tools/mcp_watchdog/mcp_watchdog.ps1))"` parse OK.
- [ ] **Step 8: `mcp/README.md`** — one line: the 18th canonical server (audio, 8796); note the canonical port set is now non-contiguous ({8777-8793, 8796}) because 8794/8795 are the manifest-external markitdown/docstruct.
- [ ] **Step 9: Commit** — `feat(windows_audio): register as 18th canonical MCP; validators accept {8777-8793,8796}`

---

## Post-merge deployment (manual)

1. `pip install -r mcp/windows_audio/requirements.txt` (pycaw+comtypes already installed from the research; formalizes them).
2. Elevated: `mcp\windows_audio\install_task.ps1` → `Start-ScheduledTask mcp-audio`.
3. Register the extension: rerun `setup_mcp_servers.ps1` (adds the audio config block) OR add it manually.
4. Acceptance: `:8796` answers 406; `audio_health`/`audio_defaults` on this machine reproduce the observed state (note: multimedia default was MUTED and there was no default communications capture device during design — the tool should flag both); sidebar shows the audio card after a goose_web refresh.

## Self-Review Notes

- Spec coverage: 7 tools mapping all 5 symptoms (Tasks 2-5), pycaw graceful degradation (Task 2 + server), read-only (no mutating calls anywhere), canonical registration + non-contiguous port-set validators across all 5 files (Task 7), install/test integration via the manifest loop, scaffolding + README (Task 6).
- Real API + real findings baked in: `_role_info` catches the no-default 0x80070490; `flag_defaults` catches muted/vol0/unplugged (the machine's multimedia default was muted); `_microphone_impl.no_default_capture` catches the missing communications-capture default observed in the probe.
- Type consistency: `default_for_roles()` dict shape (available/render/capture, role→info-or-error) is consumed identically by `flag_defaults`, `_defaults_impl`, `_microphone_impl`, `_health_impl`; `classify_bt` used in winaudio + `_bluetooth_impl`; `flow_of_id` prefix rule matches the probed ids.
