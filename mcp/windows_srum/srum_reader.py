"""SRUM reader: copy locked SRUDB.dat, parse with dissect.esedb, aggregate per app, cache.

Schema confirmed by spike — see SCHEMA.md. Key points:
- TimeStamp is an int64 whose bits are an OLE-automation date (float8), days since 1899-12-30 UTC.
- App identity via SruDbIdMapTable (IdIndex -> IdBlob, UTF-16-LE).
- App bytes = Foreground+Background BytesRead/Written; CPU = Foreground+Background CycleTime (cycles).
- Per-app energy lives in the Energy *LT* table (ActiveEnergy + CsEnergy).
"""
import os
import ctypes
import struct
import subprocess
import tempfile
import time
import datetime as dt

from dissect.esedb import EseDB

SRUDB = os.path.join(os.environ["SystemRoot"], "System32", "sru", "SRUDB.dat")
IDMAP = "SruDbIdMapTable"
T_APP = "{D10CA2FE-6FCF-4F6D-848E-B2E99266FA89}"        # App Resource Usage
T_NET = "{973F5D5C-1D90-4944-BE8E-24B94231A174}"        # Network Data Usage
T_ENERGY = "{FEE4E14F-02A9-4550-B5CE-5FA2DA202E37}LT"   # Energy Usage (long-term, per-app)
_TTL = 600  # seconds: re-copy SRUDB.dat at most every 10 min (SRUM flushes ~hourly)

_COPY = {"ts": 0.0, "path": None}
_RESULT = {}  # hours -> (ts, data)


# --- helpers ---------------------------------------------------------------
def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _cell(rec, col):
    try:
        return rec.get(col)
    except Exception:
        return None


def _decode_blob(blob):
    if blob is None:
        return None
    if isinstance(blob, (bytes, bytearray)):
        try:
            txt = blob.decode("utf-16-le", "ignore").rstrip("\x00").strip()
            return txt or blob.hex()
        except Exception:
            return blob.hex()
    return str(blob)


def _friendly(name):
    """Turn a raw IdBlob into a short app name."""
    if not isinstance(name, str):
        return None if name is None else str(name)
    s = name.strip()
    if s.startswith("!!"):
        parts = s.split("!")        # !!exe!date!hash![extra]
        if len(parts) >= 3 and parts[2]:
            return parts[2]
    if "\\" in s or "/" in s:
        return s.replace("/", "\\").rstrip("\\").split("\\")[-1] or s
    return s


def _ts_to_dt(ts):
    """SRUM TimeStamp int64-bits -> OLE date (days since 1899-12-30, UTC)."""
    if ts is None:
        return None
    if isinstance(ts, dt.datetime):
        return ts
    try:
        f = struct.unpack("<d", struct.pack("<q", int(ts)))[0]
        return dt.datetime(1899, 12, 30, tzinfo=dt.timezone.utc) + dt.timedelta(days=f)
    except Exception:
        return None


def _copy_locked():
    dst = os.path.join(tempfile.gettempdir(), "SRUDB_mcp.dat")
    try:
        if os.path.exists(dst):
            os.remove(dst)
    except OSError:
        pass
    err = ""
    # /vss handles the live file lock. Retry to ride out transient VSS contention
    # (rapid repeated snapshots can fail with a transient error).
    for _ in range(3):
        try:
            r = subprocess.run(["esentutl.exe", "/y", SRUDB, "/vss", "/d", dst],
                               capture_output=True, text=True)
        except Exception as e:
            err = str(e)
            time.sleep(1.5)
            continue
        if r.returncode == 0 and os.path.exists(dst):
            return dst
        err = ((r.stdout or "") + (r.stderr or "")).strip()
        time.sleep(1.5)
    # last resort: plain copy (only succeeds if the DB happens to be unlocked)
    try:
        r = subprocess.run(["esentutl.exe", "/y", SRUDB, "/d", dst], capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(dst):
            return dst
        err = ((r.stdout or "") + (r.stderr or "")).strip() or err
    except Exception as e:
        err = str(e)
    raise RuntimeError(f"esentutl copy failed after retries: {err[:300]}")


def _get_copy():
    now = time.time()
    if _COPY["path"] is None or not os.path.exists(_COPY["path"]) or now - _COPY["ts"] > _TTL:
        _COPY.update(ts=now, path=_copy_locked())
    return _COPY["path"]


# --- parsing ---------------------------------------------------------------
def _idmap(db):
    m = {}
    for rec in db.table(IDMAP).records():
        idx = _cell(rec, "IdIndex")
        if idx is None:
            continue
        m[idx] = _decode_blob(_cell(rec, "IdBlob"))
    return m


def _aggregate(db, table, idmap, hours, fields, key_fn):
    """fields: {out_key: [src_col, ...]} summed per app within the lookback window."""
    names = {t.name for t in db.tables()}
    if table not in names:
        return []
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
    out = {}
    for rec in db.table(table).records():
        when = _ts_to_dt(_cell(rec, "TimeStamp"))
        if when is not None and when < cutoff:
            continue
        app = _friendly(idmap.get(_cell(rec, "AppId"))) or f"id:{_cell(rec, 'AppId')}"
        agg = out.get(app)
        if agg is None:
            agg = out[app] = {"app": app, **{k: 0 for k in fields}}
        for out_key, srcs in fields.items():
            for c in srcs:
                agg[out_key] += int(_cell(rec, c) or 0)
    return sorted(out.values(), key=key_fn, reverse=True)


def _parse(path, hours=24):
    with open(path, "rb") as fh:
        db = EseDB(fh)
        idmap = _idmap(db)
        app = _aggregate(db, T_APP, idmap, hours, {
            "foreground_cycles": ["ForegroundCycleTime"],
            "background_cycles": ["BackgroundCycleTime"],
            "bytes_read": ["ForegroundBytesRead", "BackgroundBytesRead"],
            "bytes_written": ["ForegroundBytesWritten", "BackgroundBytesWritten"],
        }, key_fn=lambda r: r["foreground_cycles"] + r["background_cycles"])
        net = _aggregate(db, T_NET, idmap, hours, {
            "bytes_sent": ["BytesSent"], "bytes_recvd": ["BytesRecvd"],
        }, key_fn=lambda r: r["bytes_sent"] + r["bytes_recvd"])
        energy = _aggregate(db, T_ENERGY, idmap, hours, {
            "active_energy": ["ActiveEnergy"], "cs_energy": ["CsEnergy"],
        }, key_fn=lambda r: r["active_energy"] + r["cs_energy"])
        return {"app_usage": app, "network_usage": net, "energy_usage": energy,
                "tables": sorted(t.name for t in db.tables())}


def _cached(hours):
    now = time.time()
    ent = _RESULT.get(hours)
    if ent and now - ent[0] <= _TTL:
        return ent[1]
    data = _parse(_get_copy(), hours)
    _RESULT[hours] = (now, data)
    return data


# --- public API ------------------------------------------------------------
def _query(key, hours, top_n):
    if not is_admin():
        return {"error": "SRUM read requires admin; start the server elevated.", "is_admin": False}
    try:
        rows = _cached(int(hours))[key][:max(1, int(top_n))]
        return {"window_hours": int(hours), "count": len(rows), "rows": rows}
    except Exception as e:
        return {"error": str(e), "is_admin": True}


def app_usage(hours=24, top_n=20):
    return _query("app_usage", hours, top_n)


def network_usage(hours=24, top_n=20):
    return _query("network_usage", hours, top_n)


def energy_usage(hours=24, top_n=20):
    return _query("energy_usage", hours, top_n)


def health():
    h = {"srudb_path": SRUDB, "is_admin": is_admin(), "parser_ok": False,
         "cache_age_s": round(time.time() - _COPY["ts"]) if _COPY["path"] else None}
    try:
        st = os.stat(SRUDB)
        h["size_mb"] = round(st.st_size / 1048576, 1)
        h["last_modified"] = dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")
    except Exception as e:
        h["error"] = f"stat: {e}"
    if not h["is_admin"]:
        h["error"] = "not elevated: SRUM read requires admin (start the server elevated)"
        return h
    try:
        data = _cached(24)
        h["tables_found"] = data["tables"]
        h["row_counts"] = {"app_usage": len(data["app_usage"]),
                           "network_usage": len(data["network_usage"]),
                           "energy_usage": len(data["energy_usage"])}
        h["parser_ok"] = True
        h["cache_age_s"] = round(time.time() - _COPY["ts"]) if _COPY["path"] else 0
    except Exception as e:
        h["error"] = f"parse: {e}"
    return h
