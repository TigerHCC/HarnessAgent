"""WER report-store reader: scan Report.wer files, parse, bucket. No MCP deps.

Report.wer facts confirmed on this box:
- UTF-16 LE (BOM FF FE) and CONTAINS non-ASCII: localized Chinese `Sig[].Name` values.
  => read as encoding="utf-16"; NEVER decode via the console/cp950 path.
- Plain `Key=Value` lines. Sig[n]/DynamicSig[n]/State[n]/OsInfo[n] are index families.
- `Sig[].Name` is localized, so crash signatures are parsed by (EventType, index) POSITION,
  not by the name text. The Sig *values* are language-independent.
- `EventTime` is a Windows FILETIME (100 ns ticks since 1601-01-01 UTC).
"""
import ctypes
import datetime as dt
import os
import time

import bugchecks

# --- store locations -------------------------------------------------------
_PROGRAMDATA = os.environ.get("ProgramData", r"C:\ProgramData")
_LOCALAPPDATA = os.environ.get("LOCALAPPDATA", "")
STORE_ROOTS = [
    ("machine/ReportArchive", os.path.join(_PROGRAMDATA, "Microsoft", "Windows", "WER", "ReportArchive")),
    ("machine/ReportQueue", os.path.join(_PROGRAMDATA, "Microsoft", "Windows", "WER", "ReportQueue")),
]
if _LOCALAPPDATA:
    STORE_ROOTS += [
        ("user/ReportArchive", os.path.join(_LOCALAPPDATA, "Microsoft", "Windows", "WER", "ReportArchive")),
        ("user/ReportQueue", os.path.join(_LOCALAPPDATA, "Microsoft", "Windows", "WER", "ReportQueue")),
    ]

# EventType prefixes that count as a real crash/hang (vs install/telemetry noise).
CRASH_PREFIXES = (
    "APPCRASH", "AppCrash", "MoAppCrash", "BEX", "MoBEX", "AppHang", "MoAppHang",
    "BlueScreen", "LiveKernel", "Kernel", "InPageError", "CLR20", "StackHash", "RADAR",
)

_FILETIME_EPOCH_DIFF = 11644473600  # seconds between 1601-01-01 and 1970-01-01
_TTL = 60  # seconds; the store changes slowly
_SCAN_CACHE = {}  # (days, include_noncrash) -> (ts, [reports])

# Position maps: EventType family -> {out_field: sig_index}. Values are language-independent.
_POS_CRASH = {  # APPCRASH-style
    "app_name": 0, "app_version": 1, "app_timestamp": 2,
    "faulting_module": 3, "module_version": 4, "module_timestamp": 5,
    "exception_code": 6, "exception_offset": 7,
}
_POS_BEX = {  # BEX/BEX64: offset and code are swapped vs APPCRASH; Sig8 is overflow data
    "app_name": 0, "app_version": 1, "app_timestamp": 2,
    "faulting_module": 3, "module_version": 4, "module_timestamp": 5,
    "exception_offset": 6, "exception_code": 7, "data": 8,
}
_POS_HANG = {  # AppHangB1 / MoAppHang — no exception code
    "app_name": 0, "app_version": 1, "app_timestamp": 2,
    "hang_signature": 3, "hang_type": 4,
}


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def is_crash_type(event_type):
    if not event_type:
        return False
    return any(event_type.startswith(p) for p in CRASH_PREFIXES)


def _pos_map(event_type):
    et = event_type or ""
    if et.startswith("BEX") or et.startswith("MoBEX"):
        return _POS_BEX
    if et.startswith("AppHang") or et.startswith("MoAppHang"):
        return _POS_HANG
    if et.startswith(("APPCRASH", "AppCrash", "MoAppCrash", "InPageError", "StackHash")):
        return _POS_CRASH
    return None


_FILETIME_MAX = 2_650_467_744_000_000_000  # ~year 9999; guards huge-int division (OverflowError)


def _filetime_to_dt(ft):
    try:
        ft = int(ft)
    except (TypeError, ValueError):
        return None
    if ft <= 0 or ft > _FILETIME_MAX:
        return None
    try:
        secs = ft / 10_000_000 - _FILETIME_EPOCH_DIFF
        if secs < 0 or secs > 4102444800:  # sanity: 1970..2100
            return None
        return dt.datetime.fromtimestamp(secs, dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _parse_report_file(wer_path):
    """Parse one Report.wer into a flat dict of fields + Sig/DynamicSig/OsInfo families."""
    fields = {}
    sig, dsig, osinfo = {}, {}, {}
    with open(wer_path, "r", encoding="utf-16", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\r\n")
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            fields[key] = val
            if key.startswith("Sig[") and key.endswith("].Value"):
                idx = key[4:key.index("]")]
                if idx.isdigit():
                    sig[int(idx)] = val
            elif key.startswith("DynamicSig[") and key.endswith("].Value"):
                idx = key[11:key.index("]")]
                if idx.isdigit():
                    dsig[int(idx)] = val
            elif key.startswith("OsInfo[") and key.endswith("].Value"):
                # pair OsInfo[n].Key with OsInfo[n].Value on a second pass below
                pass
    # OsInfo needs both .Key and .Value; build from the flat fields.
    i = 0
    while True:
        k = fields.get(f"OsInfo[{i}].Key")
        if k is None:
            break
        osinfo[k] = fields.get(f"OsInfo[{i}].Value")
        i += 1
    return fields, sig, dsig, osinfo


def _typed(event_type, sig):
    """Extract language-independent typed fields from the Sig value map."""
    out = {}
    pm = _pos_map(event_type)
    if pm:
        for field, idx in pm.items():
            if idx in sig and sig[idx] not in (None, ""):
                out[field] = sig[idx]
    code = out.get("exception_code")
    if code:
        name, desc = bugchecks.describe_exception(code)
        if name:
            out["exception_name"] = name
            out["exception_desc"] = desc
    return out


def _report_record(store_label, folder, wer_path, full=False):
    try:
        fields, sig, dsig, osinfo = _parse_report_file(wer_path)
    except Exception as e:
        return {"report_id": os.path.basename(folder), "store": store_label,
                "folder": folder, "parse_error": str(e)}
    event_type = fields.get("EventType")
    typed = _typed(event_type, sig)
    when = _filetime_to_dt(fields.get("EventTime"))
    if when is None:
        try:
            when = dt.datetime.fromtimestamp(os.path.getmtime(wer_path), dt.timezone.utc)
        except OSError:
            when = None
    rec = {
        "report_id": os.path.basename(folder),
        "store": store_label,
        "folder": folder,
        "event_type": event_type,
        "friendly": fields.get("FriendlyEventName"),
        "app": fields.get("OriginalFilename") or typed.get("app_name"),
        "app_path": fields.get("AppPath"),
        "faulting_module": typed.get("faulting_module"),
        "exception_code": typed.get("exception_code"),
        "code_meaning": typed.get("exception_name"),
        "is_fatal": fields.get("IsFatal"),
        "bucket_id": fields.get("Response.BucketId"),
        "time": when.isoformat(timespec="seconds") if when else None,
        "_when": when,  # internal, stripped before returning
    }
    if full:
        rec["signatures"] = [{"index": i, "name": fields.get(f"Sig[{i}].Name"), "value": sig[i]}
                             for i in sorted(sig)]
        rec["parsed"] = typed
        rec["dynamic"] = {fields.get(f"DynamicSig[{i}].Name", str(i)): dsig[i] for i in sorted(dsig)}
        rec["os_info"] = osinfo
        rec["consent"] = fields.get("Consent")
        rec["report_status"] = fields.get("ReportStatus")
        try:
            rec["files"] = [{"name": e.name, "size": e.stat().st_size}
                            for e in os.scandir(folder) if e.is_file()]
        except OSError as e:
            rec["files_error"] = str(e)
        rec["has_dump"] = any(f["name"].lower().endswith((".dmp", ".mdmp"))
                              for f in rec.get("files", []))
    return rec


def _iter_report_dirs():
    """Yield (store_label, folder_path, wer_path) for each report, skipping permission errors."""
    for label, root in STORE_ROOTS:
        if not os.path.isdir(root):
            continue
        try:
            entries = list(os.scandir(root))
        except (PermissionError, OSError):
            continue
        for e in entries:
            try:
                if not e.is_dir():
                    continue
            except OSError:
                continue
            wer = os.path.join(e.path, "Report.wer")
            if os.path.isfile(wer):
                yield label, e.path, wer


def _scan(days, include_noncrash):
    key = (int(days), bool(include_noncrash))
    now = time.time()
    ent = _SCAN_CACHE.get(key)
    if ent and now - ent[0] <= _TTL:
        return ent[1]
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=int(days))
    out = []
    for label, folder, wer in _iter_report_dirs():
        rec = _report_record(label, folder, wer, full=False)
        if "parse_error" in rec:
            continue
        if not include_noncrash and not is_crash_type(rec["event_type"]):
            continue
        when = rec.get("_when")
        if when is not None and when < cutoff:
            continue
        out.append(rec)
    out.sort(key=lambda r: r.get("_when") or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
             reverse=True)
    # evict expired entries so the cache can't grow unbounded across varying (days) keys
    for k in [k for k, (t, _v) in _SCAN_CACHE.items() if now - t > _TTL]:
        _SCAN_CACHE.pop(k, None)
    _SCAN_CACHE[key] = (now, out)
    return out


def _strip(rec):
    return {k: v for k, v in rec.items() if not k.startswith("_")}


# --- public API ------------------------------------------------------------
def crash_summary(days=30, top_n=20, include_noncrash=False):
    try:
        reports = _scan(days, include_noncrash)
    except Exception as e:
        return {"error": str(e)}
    buckets = {}
    for r in reports:
        key = (r.get("event_type"), (r.get("app") or "").lower(),
               (r.get("faulting_module") or "").lower(), r.get("exception_code"))
        b = buckets.get(key)
        when = r.get("_when")
        if b is None:
            b = buckets[key] = {
                "event_type": r.get("event_type"), "app": r.get("app"),
                "faulting_module": r.get("faulting_module"),
                "code": r.get("exception_code"), "code_meaning": r.get("code_meaning"),
                "count": 0, "first_seen": when, "last_seen": when,
                "sample_report": r.get("report_id"),
            }
        b["count"] += 1
        if when is not None:
            if b["first_seen"] is None or when < b["first_seen"]:
                b["first_seen"] = when
            if b["last_seen"] is None or when > b["last_seen"]:
                b["last_seen"] = when
                b["sample_report"] = r.get("report_id")
    ordered = sorted(buckets.values(), key=lambda b: b["count"], reverse=True)[:max(1, int(top_n))]
    for b in ordered:
        b["first_seen"] = b["first_seen"].isoformat(timespec="seconds") if b["first_seen"] else None
        b["last_seen"] = b["last_seen"].isoformat(timespec="seconds") if b["last_seen"] else None
    return {"window_days": int(days), "total_reports": len(reports),
            "bucket_count": len(buckets), "buckets": ordered}


def _has_dump(folder):
    try:
        return any(e.is_file() and e.name.lower().endswith((".dmp", ".mdmp"))
                   for e in os.scandir(folder))
    except OSError:
        return False


def list_crashes(days=30, event_type=None, app=None, max=50):
    try:
        reports = _scan(days, include_noncrash=True)
    except Exception as e:
        return {"error": str(e)}
    try:
        cap = int(max)
    except (TypeError, ValueError):
        cap = 50
    if cap < 0:
        cap = 0
    et = (event_type or "").lower()
    ap = (app or "").lower()
    rows = []
    total_matching = 0
    for r in reports:
        if event_type and et not in (r.get("event_type") or "").lower():
            continue
        if app and ap not in (r.get("app") or "").lower():
            continue
        if not event_type and not app and not is_crash_type(r.get("event_type")):
            continue
        total_matching += 1
        if len(rows) < cap:
            rows.append(r)
    out = []
    for r in rows:
        d = _strip(r)
        d["has_dump"] = _has_dump(r.get("folder")) if r.get("folder") else False
        out.append(d)
    return {"window_days": int(days), "count": len(out), "total_matching": total_matching,
            "truncated": total_matching > len(out), "crashes": out}


def get_crash(report_id):
    if not report_id or report_id in (".", "..") or os.path.basename(report_id) != report_id:
        return {"error": "invalid report_id (must be a report folder name, no path separators)"}
    for label, root in STORE_ROOTS:
        folder = os.path.join(root, report_id)
        wer = os.path.join(folder, "Report.wer")
        if os.path.isfile(wer):
            return _strip(_report_record(label, folder, wer, full=True))
    return {"error": "report not found", "report_id": report_id}


def health():
    h = {"is_admin": is_admin(), "stores": []}
    for label, root in STORE_ROOTS:
        s = {"label": label, "path": root, "exists": os.path.isdir(root)}
        if s["exists"]:
            try:
                s["report_count"] = sum(1 for _ in os.scandir(root))
                s["readable"] = True
            except (PermissionError, OSError) as e:
                s["readable"] = False
                s["error"] = str(e)
        h["stores"].append(s)
    try:
        sample = crash_summary(days=3650, top_n=1)
        h["sample_ok"] = "error" not in sample
        h["total_crash_reports_all_time"] = sample.get("total_reports")
    except Exception as e:
        h["sample_ok"] = False
        h["sample_error"] = str(e)
    h["bugcheck_table_size"] = len(bugchecks.BUGCHECKS)
    h["exception_table_size"] = len(bugchecks.NTSTATUS_EXCEPTIONS)
    return h
