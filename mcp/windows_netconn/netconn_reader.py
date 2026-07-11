"""Live network-connection reader: psutil socket table + pid->exe + svchost pid->service. No MCP deps.

Read-only vs the system — the ONLY thing written is the JSON baseline file (data/), atomically.
psutil (7.0.0) supplies the socket table + process names; `tasklist /svc` resolves svchost -> services.

Attribution correctness notes (from adversarial review):
- Process names are resolved FRESH per enumeration (no cross-call pid->name cache) so a recycled PID is
  never attributed to a since-exited process.
- On Windows, TIME_WAIT sockets report pid 0 (no live owner), so per-process TIME_WAIT is NOT
  attributable — those sockets are counted in the system-wide by_state total but excluded from
  top_processes (they would otherwise form a phantom pid-0 "process").
- Ephemeral-port usage counts DISTINCT non-listener local ports in the dynamic range (per protocol).
"""
import csv
import ctypes
import datetime as dt
import io
import json
import os
import socket
import subprocess
import threading
import time

import psutil

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
BASELINE_PATH = os.environ.get("NETCONN_BASELINES", os.path.join(DATA_DIR, "netconn_baselines.json"))
EPHEMERAL_LOW, EPHEMERAL_HIGH = 49152, 65535  # Windows default dynamic port range

_SVC_TTL = 15
_svc_cache = {"ts": 0.0, "map": {}}
_baseline_lock = threading.Lock()


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _lim(v, default=200):
    """Non-raising limit coercion, clamped to >= 0."""
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return default


def _maybe_int(v):
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _service_map():
    """pid -> [services] via `tasklist /svc`, cached ~15 s (subprocess is expensive). Best-effort:
    services are coarse (all services hosted in a PID) and a 15 s snapshot; the exe attribution below is
    the precise, always-fresh owner."""
    now = time.time()
    if now - _svc_cache["ts"] <= _SVC_TTL and _svc_cache["map"]:
        return _svc_cache["map"]
    m = {}
    try:
        out = subprocess.run(["tasklist", "/svc", "/fo", "csv"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=20).stdout
        for r in csv.reader(io.StringIO(out)):
            if len(r) >= 3 and r[1].isdigit() and r[2] and r[2] != "N/A":
                m[int(r[1])] = [s.strip() for s in r[2].split(",") if s.strip()]
    except Exception:
        pass
    _svc_cache.update(ts=now, map=m)
    return m


def _proc_map():
    """Fresh pid -> exe map, rebuilt every enumeration (no cross-call cache -> no PID-reuse staleness)."""
    m = {}
    try:
        for p in psutil.process_iter(["pid", "name"]):
            try:
                m[p.info["pid"]] = p.info["name"]
            except Exception:
                continue
    except Exception:
        pass
    return m


def _proto(c):
    return "TCP" if c.type == socket.SOCK_STREAM else "UDP"


def _addr(a):
    if not a:
        return (None, None)
    try:
        return (a.ip, a.port)
    except AttributeError:
        return (None, None)


def _state(c, rip):
    if c.status and c.status != "NONE":
        return c.status
    # UDP has no status; a bound UDP socket with no remote is effectively a listener
    if _proto(c) == "UDP" and rip is None:
        return "LISTEN"
    return c.status


def _row(c, svcmap, pmap):
    lip, lport = _addr(c.laddr)
    rip, rport = _addr(c.raddr)
    exe = pmap.get(c.pid) if c.pid else None
    services = svcmap.get(c.pid) if c.pid else None
    return {"proto": _proto(c), "local": lip, "lport": lport, "remote": rip, "rport": rport,
            "state": _state(c, rip), "pid": c.pid, "exe": exe, "services": services}


def _all_rows():
    svcmap = _service_map()
    pmap = _proc_map()
    out = []
    for c in psutil.net_connections(kind="inet"):
        try:
            out.append(_row(c, svcmap, pmap))
        except Exception:
            continue
    return out


# --- public API ------------------------------------------------------------
def connections(state=None, proto=None, pid=None, port=None, process=None, max=200):
    try:
        rows = _all_rows()
    except Exception as e:
        return {"error": str(e)}
    lim = _lim(max)
    st = (state or "").upper()
    pr = (proto or "").upper()
    pidf = _maybe_int(pid)
    portf = _maybe_int(port)
    proc = (process or "").lower()
    out = []
    for r in rows:
        if st and (r["state"] or "").upper() != st:
            continue
        if pr and r["proto"] != pr:
            continue
        if pidf is not None and r["pid"] != pidf:
            continue
        if portf is not None and r["lport"] != portf and r["rport"] != portf:
            continue
        if proc and proc not in (r["exe"] or "").lower():
            continue
        out.append(r)
    total = len(out)
    return {"count": min(total, lim), "total_matching": total,
            "truncated": total > lim, "connections": out[:lim]}


def listeners(max=200):
    try:
        rows = _all_rows()
    except Exception as e:
        return {"error": str(e)}
    lim = _lim(max)
    out = [r for r in rows if (r["state"] or "").upper() == "LISTEN"]
    out.sort(key=lambda r: (r["proto"], r["lport"] or 0))
    total = len(out)
    return {"count": min(total, lim), "total": total, "truncated": total > lim,
            "listeners": out[:lim]}


def connection_stats():
    try:
        rows = _all_rows()
    except Exception as e:
        return {"error": str(e)}
    by_state, by_proto = {}, {}
    per_proc = {}
    ephemeral_ports = set()
    for r in rows:
        s = r["state"] or "UNKNOWN"
        by_state[s] = by_state.get(s, 0) + 1
        by_proto[r["proto"]] = by_proto.get(r["proto"], 0) + 1
        # per-process attribution: only sockets with a live owning pid (TIME_WAIT reports pid 0 on
        # Windows and is not attributable -> excluded here, still counted in by_state)
        if r["pid"]:
            key = (r["pid"], r["exe"])
            p = per_proc.setdefault(key, {"exe": r["exe"], "pid": r["pid"], "count": 0,
                                          "time_wait": 0, "close_wait": 0})
            p["count"] += 1
            if s == "TIME_WAIT":
                p["time_wait"] += 1
            elif s == "CLOSE_WAIT":
                p["close_wait"] += 1
        # ephemeral usage = DISTINCT non-listener local ports in the dynamic range (per protocol)
        if (r["state"] or "").upper() != "LISTEN" and r["lport"] and EPHEMERAL_LOW <= r["lport"] <= EPHEMERAL_HIGH:
            ephemeral_ports.add((r["proto"], r["lport"]))
    top = sorted(per_proc.values(), key=lambda p: p["count"], reverse=True)[:15]
    rng = EPHEMERAL_HIGH - EPHEMERAL_LOW + 1
    return {"total": len(rows), "by_state": by_state, "by_proto": by_proto,
            "top_processes": top,
            "ephemeral": {"range": f"{EPHEMERAL_LOW}-{EPHEMERAL_HIGH}",
                          "distinct_ports_in_use": len(ephemeral_ports),
                          "pct": round(100 * len(ephemeral_ports) / rng, 2)},
            "note": ("time_wait is per-process best-effort; on Windows TIME_WAIT sockets have no live "
                     "owner (pid 0) so they appear in by_state.TIME_WAIT but not in top_processes")}


def by_remote(ip=None, max=200):
    try:
        rows = _all_rows()
    except Exception as e:
        return {"error": str(e)}
    lim = _lim(max)
    flt = (ip or "").lower()
    out = [r for r in rows if r["remote"] and (not flt or flt in (r["remote"] or "").lower())]
    out.sort(key=lambda r: (r["remote"] or "", r["rport"] or 0))
    total = len(out)
    return {"count": min(total, lim), "total_matching": total,
            "truncated": total > lim, "connections": out[:lim]}


# --- baselines -------------------------------------------------------------
def _signatures(rows):
    sigs = set()
    for r in rows:
        if (r["state"] or "").upper() == "LISTEN":
            sigs.add(f"L|{r['proto']}|{r['local']}:{r['lport']}|{r['exe'] or '?'}")
        elif r["remote"]:
            sigs.add(f"R|{r['exe'] or '?'}|{r['remote']}:{r['rport']}")
    return sigs


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


def baseline_save(name="default"):
    try:
        rows = _all_rows()
    except Exception as e:
        return {"error": str(e)}
    sigs = sorted(_signatures(rows))
    with _baseline_lock:
        data = _load_baselines()
        data[name] = {"ts": _now_iso(), "signatures": sigs}
        try:
            _save_baselines(data)
        except OSError as e:
            return {"error": f"could not write baseline: {e}"}
    return {"name": name, "ts": data[name]["ts"], "signature_count": len(sigs)}


def baseline_diff(name="default"):
    with _baseline_lock:
        data = _load_baselines()
    entry = data.get(name)
    if not isinstance(entry, dict) or "signatures" not in entry:
        return {"error": f"no valid baseline named '{name}'; call baseline_save first",
                "available": list(data.keys())}
    try:
        rows = _all_rows()
    except Exception as e:
        return {"error": str(e)}
    base = set(entry.get("signatures") or [])
    cur = _signatures(rows)
    added = sorted(cur - base)
    removed = sorted(base - cur)
    return {"name": name, "baseline_ts": entry.get("ts"),
            "added": added[:500], "removed": removed[:500],
            "summary": {"added": len(added), "removed": len(removed)},
            "truncated": len(added) > 500 or len(removed) > 500}


def health():
    h = {"is_admin": is_admin()}
    try:
        rows = _all_rows()
        h["psutil_ok"] = True
        h["socket_count"] = len(rows)
    except Exception as e:
        h["psutil_ok"] = False
        h["error"] = str(e)
    h["service_map_ok"] = bool(_service_map())
    with _baseline_lock:
        h["baselines"] = list(_load_baselines().keys())
    return h
