"""Disk health + volume state via Windows Storage cmdlets / fsutil. No MCP deps.

Read-only: only *queries* (Get-PhysicalDisk / Get-StorageReliabilityCounter / fsutil dirty query /
fsutil repair state / Win32_ShadowCopy). The only write is the JSON health baseline (data/).
Verified: NVMe SK hynix Healthy, Temperature 60, Wear 0; C: NOT Dirty / Clean; 2 shadow copies.
"""
import datetime as dt
import json
import os
import subprocess
import threading

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
BASELINE_PATH = os.environ.get("DISK_BASELINES", os.path.join(DATA_DIR, "disk_baselines.json"))
_lock = threading.Lock()
_PS = ["powershell", "-NoProfile", "-NonInteractive", "-Command"]


def _now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _ps_json(cmd, timeout=30):
    """Run a PowerShell snippet that emits JSON; return the parsed object (or raise)."""
    full = cmd + " | ConvertTo-Json -Depth 4 -Compress"
    r = subprocess.run(_PS + [full], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    out = (r.stdout or "").strip()
    if not out:
        return None
    data = json.loads(out)
    return data


def _as_list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


# --- disk health (SMART / reliability) -------------------------------------
def disk_health():
    # Pipe each PhysicalDisk object STRAIGHT into Get-StorageReliabilityCounter (Microsoft's documented
    # pattern) so the counter always attaches to the right disk -- no DeviceId/DeviceNumber id matching.
    cmd = (
        "Get-PhysicalDisk | ForEach-Object { $d=$_; "
        "$rc = $d | Get-StorageReliabilityCounter -ErrorAction SilentlyContinue; "
        "[PSCustomObject]@{ DeviceId=$d.DeviceId; FriendlyName=$d.FriendlyName; "
        "MediaType=[string]$d.MediaType; HealthStatus=[string]$d.HealthStatus; "
        "OperationalStatus=[string]$d.OperationalStatus; SizeGB=[math]::Round($d.Size/1GB); "
        "Wear=$rc.Wear; Temperature=$rc.Temperature; ReadErrorsTotal=$rc.ReadErrorsTotal; "
        "WriteErrorsTotal=$rc.WriteErrorsTotal; PowerOnHours=$rc.PowerOnHours; "
        "ReadLatencyMax=$rc.ReadLatencyMax; WriteLatencyMax=$rc.WriteLatencyMax } }")
    try:
        disks = _as_list(_ps_json(cmd))
    except Exception as e:
        return {"error": f"Get-PhysicalDisk failed: {e}"}
    out = []
    for d in disks:
        out.append({
            "device_id": d.get("DeviceId"), "friendly_name": d.get("FriendlyName"),
            "media_type": d.get("MediaType"), "health": d.get("HealthStatus"),
            "operational": d.get("OperationalStatus"), "size_gb": d.get("SizeGB"),
            "wear_pct": d.get("Wear"), "temperature_c": d.get("Temperature"),
            "read_errors": d.get("ReadErrorsTotal"), "write_errors": d.get("WriteErrorsTotal"),
            "power_on_hours": d.get("PowerOnHours"),
            "read_latency_max_ms": d.get("ReadLatencyMax"), "write_latency_max_ms": d.get("WriteLatencyMax"),
        })
    return {"disks": out, "count": len(out)}


# --- volume state ----------------------------------------------------------
def _valid_volume(v):
    return isinstance(v, str) and len(v) == 2 and v[0].isalpha() and v[1] == ":"


def volume_state(volume="C:"):
    if not _valid_volume(volume):
        return {"error": "invalid volume (expected e.g. 'C:')"}
    res = {"volume": volume}
    # dirty bit: locale-independent FSCTL_IS_VOLUME_DIRTY (fsutil's text output is localized)
    try:
        import usn_reader
        res["dirty"] = usn_reader.is_volume_dirty(volume)
    except Exception as e:
        res["dirty_error"] = str(e)
    try:
        r = subprocess.run(_PS + [f"fsutil repair state {volume}"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=15)
        # parse the hex corruption-state code (locale-independent); 0x0 == clean
        import re
        m = re.search(r"0x([0-9A-Fa-f]+)", r.stdout or "")
        if m:
            code = int(m.group(1), 16)
            res["repair_state"] = {"code": f"0x{code:X}", "clean": code == 0}
        else:
            res["repair_state"] = {"raw": (r.stdout or "").strip()[:80]}
    except Exception as e:
        res["repair_error"] = str(e)
    try:
        vol = _ps_json(f"Get-Volume -DriveLetter {volume[0]} | Select-Object "
                       "@{n='FreeGB';e={[math]::Round($_.SizeRemaining/1GB,1)}},"
                       "@{n='SizeGB';e={[math]::Round($_.Size/1GB,1)}},FileSystemType,HealthStatus")
        if vol:
            res.update({"free_gb": vol.get("FreeGB"), "size_gb": vol.get("SizeGB"),
                        "filesystem": vol.get("FileSystemType"), "health": vol.get("HealthStatus")})
    except Exception as e:
        res["volume_error"] = str(e)
    try:
        sc = _ps_json("Get-CimInstance Win32_ShadowCopy | Measure-Object | Select-Object Count")
        res["shadow_copies"] = sc.get("Count") if sc else 0
    except Exception:
        res["shadow_copies"] = None
    return res


# --- health baselines (trend) ----------------------------------------------
def _load():
    try:
        with open(BASELINE_PATH, "r", encoding="utf-8") as fh:
            obj = json.load(fh)
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data):
    d = os.path.dirname(BASELINE_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = BASELINE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, BASELINE_PATH)


_TREND_FIELDS = ("wear_pct", "temperature_c", "read_errors", "write_errors", "power_on_hours")


def health_baseline_save(name="default"):
    h = disk_health()
    if "error" in h:
        return h
    snap = {d.get("device_id"): {k: d.get(k) for k in _TREND_FIELDS} for d in h["disks"]}
    with _lock:
        data = _load()
        data[name] = {"ts": _now_iso(), "disks": snap}
        try:
            _save(data)
        except OSError as e:
            return {"error": f"could not write baseline: {e}"}
    return {"name": name, "ts": data[name]["ts"], "disk_count": len(snap)}


def health_baseline_diff(name="default"):
    with _lock:
        data = _load()
    entry = data.get(name)
    if not isinstance(entry, dict) or not isinstance(entry.get("disks"), dict):
        return {"error": f"no valid baseline named '{name}'; call health_baseline_save first",
                "available": list(data.keys())}
    h = disk_health()
    if "error" in h:
        return h
    now = {str(d.get("device_id")): d for d in h["disks"]}
    deltas = {}
    for dev, base in entry["disks"].items():
        cur = now.get(str(dev))
        if not cur or not isinstance(base, dict):
            continue
        dd = {}
        for f in _TREND_FIELDS:
            b, n = base.get(f), cur.get(f)
            if isinstance(b, (int, float)) and isinstance(n, (int, float)):
                dd[f] = {"from": b, "to": n, "delta": round(n - b, 3)}
        deltas[dev] = dd
    return {"name": name, "baseline_ts": entry.get("ts"), "deltas": deltas}


def health():
    return {"baselines": list(_load().keys()), "baseline_path": BASELINE_PATH}
