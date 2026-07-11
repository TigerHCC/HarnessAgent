"""PDH (Performance Data Helper) reader: locale-safe real-time system counters. No MCP deps.

Uses pdh.dll via ctypes with PdhAddEnglishCounterW so counter paths work on non-English Windows
(the localized counter names otherwise break scripting). Rate counters (e.g. % Processor Utility,
Avg Disk sec/Transfer, Pages/sec) require TWO samples separated by an interval — snapshot() does both.
Complements SRUM's psutil snapshot with the metrics psutil doesn't expose: disk LATENCY, pool
nonpaged/paged (kernel-leak detection), hard-paging, and % Processor Utility (Task-Manager-accurate).
"""
import ctypes
import ctypes.wintypes as wt
import datetime as dt
import json
import os
import threading
import time

_pdh = ctypes.windll.pdh
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
BASELINE_PATH = os.environ.get("PERFMON_BASELINES", os.path.join(DATA_DIR, "perfmon_baselines.json"))
_baseline_lock = threading.Lock()
# NOTE: only single-instance counters are supported. Wildcard/multi-instance paths (e.g.
# "\GPU Engine(*)\Utilization Percentage") need PdhGetFormattedCounterArray, which is not implemented;
# such a path is added successfully but the single-value read returns PDH_MORE_DATA -> None + error.
_PDH_FMT_DOUBLE = 0x00000200


class _PDH_FMT_COUNTERVALUE_DOUBLE(ctypes.Structure):
    # DWORD CStatus; (4B pad on x64); double doubleValue  -> doubleValue at offset 8
    _fields_ = [("CStatus", wt.DWORD), ("doubleValue", ctypes.c_double)]


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# Curated counter set. key -> (english path, unit, is_rate). _Total instances avoid wildcard expansion.
COUNTERS = {
    "cpu_utility_pct":        (r"\Processor Information(_Total)\% Processor Utility", "%", True),
    "cpu_privileged_pct":     (r"\Processor Information(_Total)\% Privileged Time", "%", True),
    "cpu_queue_length":       (r"\System\Processor Queue Length", "threads", False),
    "context_switches_sec":   (r"\System\Context Switches/sec", "/s", True),
    "disk_sec_per_read":      (r"\PhysicalDisk(_Total)\Avg. Disk sec/Read", "s", True),
    "disk_sec_per_write":     (r"\PhysicalDisk(_Total)\Avg. Disk sec/Write", "s", True),
    "disk_sec_per_transfer":  (r"\PhysicalDisk(_Total)\Avg. Disk sec/Transfer", "s", True),
    "disk_queue_length":      (r"\PhysicalDisk(_Total)\Current Disk Queue Length", "ops", False),
    "disk_idle_pct":          (r"\PhysicalDisk(_Total)\% Idle Time", "%", True),
    "mem_available_mb":       (r"\Memory\Available MBytes", "MB", False),
    "mem_committed_pct":      (r"\Memory\% Committed Bytes In Use", "%", False),
    "mem_pool_nonpaged_mb":   (r"\Memory\Pool Nonpaged Bytes", "MB", False),
    "mem_pool_paged_mb":      (r"\Memory\Pool Paged Bytes", "MB", False),
    "mem_pages_sec":          (r"\Memory\Pages/sec", "/s", True),
    "mem_page_faults_sec":    (r"\Memory\Page Faults/sec", "/s", True),
}
# byte-valued counters we convert to MB in the output
_BYTES_TO_MB = {"mem_pool_nonpaged_mb", "mem_pool_paged_mb"}


def read_counters(keys=None, paths=None, delay_ms=1000):
    """Read a set of counters. Returns {key: value|None}. `keys` selects from COUNTERS; `paths` is a
    dict of {name: english_path} for ad-hoc counters. Rate counters are double-sampled with delay_ms."""
    sel = {}
    if keys is None and paths is None:
        keys = list(COUNTERS.keys())
    for k in (keys or []):
        if k in COUNTERS:
            sel[k] = COUNTERS[k][0]
    if paths:
        sel.update(paths)
    if not sel:
        return {}, {}

    q = wt.HANDLE()
    if _pdh.PdhOpenQueryW(None, 0, ctypes.byref(q)) != 0:
        raise OSError("PdhOpenQuery failed")
    handles, status = {}, {}
    try:
        for name, path in sel.items():
            h = wt.HANDLE()
            rc = _pdh.PdhAddEnglishCounterW(q, path, 0, ctypes.byref(h))
            if rc == 0:
                handles[name] = h
            else:
                status[name] = f"add failed 0x{rc & 0xffffffff:08X}"
        if _pdh.PdhCollectQueryData(q) != 0:
            raise OSError("PdhCollectQueryData failed (sample 1)")
        time.sleep(max(0, delay_ms) / 1000.0)
        if _pdh.PdhCollectQueryData(q) != 0:  # second sample for rate counters
            status["_second_sample"] = "PdhCollectQueryData failed (sample 2); rate counters may be null"
        out = {}
        for name, h in handles.items():
            ct = wt.DWORD()
            val = _PDH_FMT_COUNTERVALUE_DOUBLE()
            rc = _pdh.PdhGetFormattedCounterValue(h, _PDH_FMT_DOUBLE, ctypes.byref(ct), ctypes.byref(val))
            if rc != 0:
                out[name] = None
                status[name] = f"read failed 0x{rc & 0xffffffff:08X}"
                continue
            v = val.doubleValue
            if name in _BYTES_TO_MB:
                v = v / 1048576.0
            out[name] = round(v, 3)
        for name, err in status.items():
            out.setdefault(name, None)
        return out, status
    finally:
        _pdh.PdhCloseQuery(q)


def snapshot(delay_ms=1000):
    values, status = read_counters(delay_ms=delay_ms)
    grouped = {"cpu": {}, "disk": {}, "memory": {}, "system": {}}
    prefix_group = {"cpu_": "cpu", "disk_": "disk", "mem_": "memory",
                    "context_": "system"}
    for k, v in values.items():
        g = "system"
        for pfx, grp in prefix_group.items():
            if k.startswith(pfx):
                g = grp
                break
        grouped[g][k] = v
    res = {"sampled_ms": delay_ms, "counters": grouped}
    if status:
        res["counter_errors"] = status
    return res


# thresholds for the bottleneck heuristic
_THRESHOLDS = {
    "cpu_utility_pct": (85, "CPU saturated (% Processor Utility high)"),
    "disk_sec_per_transfer": (0.025, "Disk latency high (Avg Disk sec/Transfer > 25 ms)"),
    "disk_queue_length": (2, "Disk queue backed up"),
    "mem_available_mb": (500, "Low available memory", "below"),
    "mem_committed_pct": (90, "Commit charge pressure"),
    "mem_pages_sec": (1000, "Hard paging (Pages/sec high)"),
}


def bottleneck(delay_ms=1000):
    values, status = read_counters(delay_ms=delay_ms)
    findings = []
    for key, spec in _THRESHOLDS.items():
        v = values.get(key)
        if v is None:
            continue
        threshold, msg = spec[0], spec[1]
        below = len(spec) > 2 and spec[2] == "below"
        hit = (v < threshold) if below else (v > threshold)
        if hit:
            findings.append({"metric": key, "value": v, "threshold": threshold, "note": msg})
    verdict = "healthy" if not findings else "; ".join(f["note"] for f in findings)
    res = {"verdict": verdict, "findings": findings,
           "checked": {k: values.get(k) for k in _THRESHOLDS}}
    if status:
        res["counter_errors"] = status
    return res


def _now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _load_baselines():
    try:
        with open(BASELINE_PATH, "r", encoding="utf-8") as fh:
            obj = json.load(fh)
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_baselines(data):
    d = os.path.dirname(BASELINE_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = BASELINE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, BASELINE_PATH)


def baseline_save(name="default", delay_ms=1000):
    values, status = read_counters(delay_ms=delay_ms)
    with _baseline_lock:
        data = _load_baselines()
        data[name] = {"ts": _now_iso(), "values": values}
        try:
            _save_baselines(data)
        except OSError as e:
            return {"error": f"could not write baseline: {e}"}
    res = {"name": name, "ts": data[name]["ts"], "counter_count": len(values)}
    if status:
        res["counter_errors"] = status
    return res


def baseline_diff(name="default", delay_ms=1000):
    with _baseline_lock:
        data = _load_baselines()
    entry = data.get(name)
    if not isinstance(entry, dict) or not isinstance(entry.get("values"), dict):
        return {"error": f"no valid baseline named '{name}'; call baseline_save first",
                "available": list(data.keys())}
    base = entry["values"]
    now, status = read_counters(delay_ms=delay_ms)
    deltas = {}
    for k in COUNTERS:
        b, n = base.get(k), now.get(k)
        if isinstance(b, (int, float)) and isinstance(n, (int, float)):
            deltas[k] = {"from": b, "to": n, "delta": round(n - b, 3)}
    res = {"name": name, "baseline_ts": entry.get("ts"), "now": now, "deltas": deltas}
    if status:
        res["counter_errors"] = status
    return res


def health():
    h = {"is_admin": is_admin()}
    try:
        values, status = read_counters(keys=["cpu_utility_pct", "mem_available_mb"], delay_ms=200)
        h["pdh_ok"] = values.get("cpu_utility_pct") is not None
        h["sample"] = values
        if status:
            h["counter_errors"] = status
    except Exception as e:
        h["pdh_ok"] = False
        h["error"] = str(e)
    h["counter_count"] = len(COUNTERS)
    return h
