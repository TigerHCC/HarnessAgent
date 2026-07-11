"""Registry execution-evidence reader: BAM, UserAssist, ShimCache. No MCP deps.

All values are binary blobs / ROT13 strings / raw FILETIMEs that reg.exe cannot usefully show.
Verified on this box:
- BAM: HKLM\\SYSTEM\\CurrentControlSet\\Services\\bam\\State\\UserSettings\\<SID> — value name is a
  device path, value data[:8] = last-exec FILETIME.
- UserAssist: HKCU\\...\\Explorer\\UserAssist\\{GUID}\\Count — names ROT13; 72-byte entries with
  run_count@0x04, focus_ms@0x0C, last-run FILETIME@0x3C.
- ShimCache: HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\AppCompatCache\\AppCompatCache
  REG_BINARY: header 0x34; entries signature '10ts'; path + last-modified FILETIME (file $SI mtime,
  which is PRESENCE evidence, not execution time).
"""
import codecs
import ctypes
import datetime as dt
import struct
import winreg

try:
    import win32security
except Exception:  # pragma: no cover
    win32security = None

_FILETIME_MAX = 2_650_467_744_000_000_000
_WELL_KNOWN_SIDS = {
    "S-1-5-18": "SYSTEM", "S-1-5-19": "LOCAL SERVICE", "S-1-5-20": "NETWORK SERVICE",
}


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _ft_to_iso(ft):
    if ft is None or ft <= 0 or ft > _FILETIME_MAX:
        return None
    try:
        secs = ft / 10_000_000 - 11644473600
        if secs < 0 or secs > 4102444800:
            return None
        return dt.datetime.fromtimestamp(secs, dt.timezone.utc).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None


def _sid_to_name(sid_str):
    if sid_str in _WELL_KNOWN_SIDS:
        return _WELL_KNOWN_SIDS[sid_str]
    if win32security is None:
        return sid_str
    try:
        sid = win32security.ConvertStringSidToSid(sid_str)
        name, domain, _ = win32security.LookupAccountSid(None, sid)
        return f"{domain}\\{name}" if domain else name
    except Exception:
        return sid_str


def _drive_map():
    """Map \\Device\\HarddiskVolumeN (and \\Volume{..}) prefixes to drive letters (best-effort)."""
    m = {}
    buf = ctypes.create_unicode_buffer(1024)
    for i in range(26):
        letter = f"{chr(65 + i)}:"
        try:
            n = ctypes.windll.kernel32.QueryDosDeviceW(letter, buf, 1024)
        except Exception:
            n = 0
        if n:
            m[buf.value.lower()] = letter
    return m


def _map_device_path(path, dm):
    if not path:
        return path
    low = path.lower()
    for dev, letter in dm.items():
        if low.startswith(dev.lower() + "\\"):
            return letter + path[len(dev):]
    return path


def _open64(hive, subkey):
    return winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)


# --- BAM -------------------------------------------------------------------
_BAM_ROOTS = [
    r"SYSTEM\CurrentControlSet\Services\bam\State\UserSettings",
    r"SYSTEM\CurrentControlSet\Services\bam\UserSettings",  # older layout
]


def bam_list(max=200):
    if not is_admin():
        return {"error": "BAM read requires admin (SYSTEM hive); start the server elevated.", "is_admin": False}
    dm = _drive_map()
    rows = []
    used_root = None
    for root in _BAM_ROOTS:
        try:
            base = _open64(winreg.HKEY_LOCAL_MACHINE, root)
            used_root = root
        except OSError:
            continue
        i = 0
        while True:
            try:
                sid = winreg.EnumKey(base, i)
            except OSError:
                break
            i += 1
            user = _sid_to_name(sid)
            try:
                sub = _open64(winreg.HKEY_LOCAL_MACHINE, root + "\\" + sid)
            except OSError:
                continue
            nvals = winreg.QueryInfoKey(sub)[1]
            for j in range(nvals):
                try:
                    name, val, _typ = winreg.EnumValue(sub, j)
                except OSError:
                    continue
                if not isinstance(val, (bytes, bytearray)) or len(val) < 8:
                    continue
                (ft,) = struct.unpack_from("<Q", val, 0)
                iso = _ft_to_iso(ft)
                if iso is None:
                    continue
                rows.append({"exe": name.rsplit("\\", 1)[-1], "path": _map_device_path(name, dm),
                             "last_exec": iso, "sid": sid, "user": user, "_ft": ft})
        break  # first existing root wins
    if used_root is None:
        return {"error": "BAM key not found", "roots_tried": _BAM_ROOTS}
    rows.sort(key=lambda r: r["_ft"], reverse=True)
    total = len(rows)
    out = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows[:int(max)]]
    return {"count": len(out), "total": total, "truncated": total > len(out), "bam": out}


# --- UserAssist ------------------------------------------------------------
_UA_ROOT = r"Software\Microsoft\Windows\CurrentVersion\Explorer\UserAssist"
_UA_SKIP = ("UEME_CTLSESSION", "UEME_CTLCUACount", "UEME_CTLPRELAUNCH")


def userassist_list(max=200):
    try:
        base = _open64(winreg.HKEY_CURRENT_USER, _UA_ROOT)
    except OSError as e:
        return {"error": f"UserAssist not readable: {e}"}
    rows = []
    i = 0
    while True:
        try:
            guid = winreg.EnumKey(base, i)
        except OSError:
            break
        i += 1
        try:
            cnt = _open64(winreg.HKEY_CURRENT_USER, _UA_ROOT + "\\" + guid + "\\Count")
        except OSError:
            continue
        nvals = winreg.QueryInfoKey(cnt)[1]
        for j in range(nvals):
            try:
                name, val, _typ = winreg.EnumValue(cnt, j)
            except OSError:
                continue
            try:
                decoded = codecs.decode(name, "rot13")
            except Exception:
                decoded = name
            if any(s in decoded for s in _UA_SKIP):
                continue
            if not isinstance(val, (bytes, bytearray)) or len(val) < 68:
                continue
            run_count = struct.unpack_from("<I", val, 0x04)[0]
            focus_ms = struct.unpack_from("<I", val, 0x0C)[0]
            (ft,) = struct.unpack_from("<Q", val, 0x3C)
            iso = _ft_to_iso(ft)
            if iso is None:
                continue  # skip entries with no valid last-run time (don't let garbage sort to the top)
            rows.append({"name": decoded, "run_count": run_count, "last_run": iso,
                         "focus_seconds": round(focus_ms / 1000, 1) if focus_ms < 0xFFFFFFFF else None,
                         "_ft": ft})
    rows.sort(key=lambda r: (r["_ft"], r["run_count"]), reverse=True)
    total = len(rows)
    out = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows[:int(max)]]
    return {"count": len(out), "total": total, "truncated": total > len(out), "userassist": out}


# --- ShimCache (AppCompatCache) --------------------------------------------
_SHIM_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\AppCompatCache"
_SHIM_SIG = b"10ts"


def _parse_shimcache(blob, filter=None, max=200):
    """Returns (rows, parsed, matched): parsed = every 10ts entry seen; matched = entries passing the
    filter; rows = matched entries actually returned (capped at max)."""
    rows = []
    if len(blob) < 4:
        return rows, 0, 0
    (hdr_len,) = struct.unpack_from("<I", blob, 0)
    if not (0 < hdr_len < len(blob)):
        hdr_len = 0x34
    off = hdr_len
    idx = 0
    parsed = 0
    matched = 0
    flt = (filter or "").lower()
    while off + 14 <= len(blob):
        if blob[off:off + 4] != _SHIM_SIG:
            break
        try:
            (entry_size,) = struct.unpack_from("<I", blob, off + 8)
            (path_size,) = struct.unpack_from("<H", blob, off + 12)
        except struct.error:
            break
        path_start = off + 14
        path_end = path_start + path_size
        if path_size > 0x2000 or path_end + 8 > len(blob):
            break
        path = blob[path_start:path_end].decode("utf-16-le", "replace").rstrip("\x00")
        # some entries (packaged/UWP apps) store tab-separated metadata instead of a path;
        # collapse control chars so the output stays readable
        path = "".join(c if c >= " " else " " for c in path).strip()
        (ft,) = struct.unpack_from("<Q", blob, path_end)
        parsed += 1
        step = 12 + entry_size if 0 < entry_size < len(blob) else path_end + 12 - off
        if step <= 0:
            break
        if not flt or flt in path.lower():
            matched += 1
            if len(rows) < int(max):
                rows.append({"path": path, "last_modified": _ft_to_iso(ft), "position": idx})
        idx += 1
        off += step
    return rows, parsed, matched


def shimcache_list(filter=None, max=200):
    if not is_admin():
        return {"error": "ShimCache read requires admin (SYSTEM hive); start the server elevated.", "is_admin": False}
    try:
        k = _open64(winreg.HKEY_LOCAL_MACHINE, _SHIM_KEY)
        blob, _ = winreg.QueryValueEx(k, "AppCompatCache")
    except OSError as e:
        return {"error": f"ShimCache not readable: {e}"}
    try:
        rows, parsed, matched = _parse_shimcache(bytes(blob), filter=filter, max=max)
    except Exception as e:
        return {"error": f"parse failed: {e}"}
    # truncated reflects whether MATCHING rows were capped by max — not filtered-out entries
    return {"count": len(rows), "entries_parsed": parsed, "matched": matched,
            "truncated": matched > len(rows),
            "note": "last_modified is the file's $StandardInfo mtime (presence evidence), NOT execution time",
            "shimcache": rows}


def iter_bam_execs():
    r = bam_list(max=100000)
    for row in r.get("bam", []):
        yield ("BAM", row["exe"], row["last_exec"], row.get("user"))


def iter_userassist_runs():
    r = userassist_list(max=100000)
    for row in r.get("userassist", []):
        if row.get("last_run"):
            yield ("UserAssist", row["name"], row["last_run"], row.get("run_count"))


def health():
    h = {"is_admin": is_admin()}
    try:
        b = bam_list(max=100000)
        h["bam"] = {"error": b["error"]} if "error" in b else {"entries": b["total"]}
    except Exception as e:
        h["bam"] = {"error": str(e)}
    try:
        u = userassist_list(max=100000)
        h["userassist"] = {"error": u["error"]} if "error" in u else {"entries": u["total"]}
    except Exception as e:
        h["userassist"] = {"error": str(e)}
    try:
        s = shimcache_list(max=1)
        h["shimcache"] = {"error": s["error"]} if "error" in s else {"entries_parsed": s["entries_parsed"]}
    except Exception as e:
        h["shimcache"] = {"error": str(e)}
    return h
