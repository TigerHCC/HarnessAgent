"""NTFS USN change-journal reader via ctypes DeviceIoControl. No MCP deps.

READ-ONLY: the volume handle is GENERIC_READ; nothing is ever written to the volume.
Verified on this box: QUERY_USN_JOURNAL + READ_USN_JOURNAL from max(lowest, next-window) returns
USN_RECORD_V2 records (RENAME_NEW etc.) with correct timestamps + names. FRN->path via OpenFileById +
GetFinalPathNameByHandleW.

ctypes note: restype/argtypes are set so 64-bit HANDLEs are NOT truncated (a classic bug).
"""
import ctypes
import datetime as dt
import os
import struct
from ctypes import wintypes, c_void_p, POINTER, byref

_k = ctypes.WinDLL("kernel32", use_last_error=True)
_k.CreateFileW.restype = wintypes.HANDLE
_k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, c_void_p,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
_k.DeviceIoControl.restype = wintypes.BOOL
_k.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, c_void_p, wintypes.DWORD,
                               c_void_p, wintypes.DWORD, POINTER(wintypes.DWORD), c_void_p]
_k.OpenFileById.restype = wintypes.HANDLE
_k.OpenFileById.argtypes = [wintypes.HANDLE, c_void_p, wintypes.DWORD, wintypes.DWORD,
                            c_void_p, wintypes.DWORD]
_k.GetFinalPathNameByHandleW.restype = wintypes.DWORD
_k.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
_k.CloseHandle.restype = wintypes.BOOL
_k.CloseHandle.argtypes = [wintypes.HANDLE]

_GENERIC_READ = 0x80000000
_FILE_READ_ATTRIBUTES = 0x80
_FILE_SHARE_RW_D = 0x00000007  # READ|WRITE|DELETE
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FSCTL_QUERY_USN_JOURNAL = 0x000900F4
_FSCTL_READ_USN_JOURNAL = 0x000900BB
_INVALID = wintypes.HANDLE(-1).value

_OUT_BUF = 1 << 20          # 1 MB read buffer
# USN reads only accept StartUsn == 0 or an actual record boundary -- an arbitrary mid-journal offset
# returns ERROR_INVALID_PARAMETER (87). So to get "recent" changes we read the whole journal FORWARD from
# the first record and filter by timestamp (older records are skipped cheaply, no path resolution). This
# journal is ~40 MB (~1 s). _MAX_BYTES is only a runaway backstop for a pathologically huge journal.
_MAX_BYTES = 2048 << 20     # runaway backstop on journal bytes scanned per call (covers realistic journals)
_MAX_RECORDS = 200000
_FSCTL_IS_VOLUME_DIRTY = 0x00090078
_FILETIME_MAX = 2_650_467_744_000_000_000

_REASONS = {
    0x00000001: "DATA_OVERWRITE", 0x00000002: "DATA_EXTEND", 0x00000004: "DATA_TRUNCATION",
    0x00000010: "NAMED_DATA_OVERWRITE", 0x00000100: "FILE_CREATE", 0x00000200: "FILE_DELETE",
    0x00000400: "RENAME_OLD_NAME", 0x00000800: "RENAME_NEW_NAME", 0x00001000: "INDEXABLE_CHANGE",
    0x00002000: "BASIC_INFO_CHANGE", 0x00004000: "HARD_LINK_CHANGE", 0x00008000: "COMPRESSION_CHANGE",
    0x00010000: "ENCRYPTION_CHANGE", 0x00020000: "OBJECT_ID_CHANGE", 0x00040000: "REPARSE_POINT_CHANGE",
    0x00080000: "STREAM_CHANGE", 0x00100000: "TRANSACTED_CHANGE", 0x00200000: "INTEGRITY_CHANGE",
    0x80000000: "CLOSE",
}


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _ft_to_dt(ft):
    if ft is None or ft <= 0 or ft > _FILETIME_MAX:
        return None
    try:
        secs = ft / 10_000_000 - 11644473600
        if secs < 0 or secs > 4102444800:
            return None
        return dt.datetime.fromtimestamp(secs, dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _reason_names(mask):
    return [name for bit, name in _REASONS.items() if mask & bit]


def _valid_volume(volume):
    return isinstance(volume, str) and len(volume) == 2 and volume[0].isalpha() and volume[1] == ":"


def _open_volume(volume):
    path = "\\\\.\\" + volume
    h = _k.CreateFileW(path, _GENERIC_READ, _FILE_SHARE_RW_D, None, _OPEN_EXISTING, 0, None)
    if h == _INVALID or not h:
        raise OSError(f"cannot open {path} (err {ctypes.get_last_error()}; needs admin)")
    return h


def _query_journal(h):
    out = ctypes.create_string_buffer(80)
    br = wintypes.DWORD(0)
    if not _k.DeviceIoControl(h, _FSCTL_QUERY_USN_JOURNAL, None, 0, out, 80, byref(br), None):
        raise OSError(f"QUERY_USN_JOURNAL failed (err {ctypes.get_last_error()})")
    jid, first, nxt, lowest, maxusn = struct.unpack_from("<Qqqqq", out, 0)
    return {"journal_id": jid, "first_usn": first, "next_usn": nxt, "lowest_valid_usn": lowest}


def _resolve_path(h_vol, parent_ref, cache, is_v3):
    if parent_ref in cache:
        return cache[parent_ref]
    path = None
    try:
        if is_v3:
            desc = struct.pack("<II16s", 24, 2, parent_ref)  # ExtendedFileIdType, 128-bit
        else:
            desc = struct.pack("<IIQ8x", 24, 0, parent_ref)  # FileIdType, 64-bit + pad
        buf = ctypes.create_string_buffer(len(desc))
        buf.raw = desc
        hf = _k.OpenFileById(h_vol, buf, _FILE_READ_ATTRIBUTES, _FILE_SHARE_RW_D,
                             None, _FILE_FLAG_BACKUP_SEMANTICS)
        if hf and hf != _INVALID:
            try:
                out = ctypes.create_unicode_buffer(1024)
                n = _k.GetFinalPathNameByHandleW(hf, out, 1024, 0)
                if 0 < n < 1024:
                    p = out.value
                    if p.startswith("\\\\?\\"):
                        p = p[4:]
                    path = p
            finally:
                _k.CloseHandle(hf)
    except Exception:
        path = None
    cache[parent_ref] = path
    return path


def _read_records(volume, minutes, want_path=True, stats=None):
    """Generator of parsed USN records within the lookback window. Raises OSError on volume/journal error.
    If `stats` (a dict) is given, sets stats['scan_truncated']=True when the byte backstop is hit before
    reaching the tail (so the caller can flag that recent changes may be incomplete)."""
    h = _open_volume(volume)
    pcache = {}
    try:
        j = _query_journal(h)
        jid, first, nxt, lowest = j["journal_id"], j["first_usn"], j["next_usn"], j["lowest_valid_usn"]
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=int(minutes))
        # start at the first valid record (0 also means "beginning"); read forward to the end
        start = first if first > 0 else 0
        scanned = 0
        emitted = 0
        retried_from_zero = False
        outbuf = ctypes.create_string_buffer(_OUT_BUF)
        while scanned < _MAX_BYTES and emitted < _MAX_RECORDS:
            inp = struct.pack("<qIIQQQ", start, 0xFFFFFFFF, 0, 0, 0, jid)
            br = wintypes.DWORD(0)
            ok = _k.DeviceIoControl(h, _FSCTL_READ_USN_JOURNAL, inp, len(inp),
                                    outbuf, _OUT_BUF, byref(br), None)
            if not ok:
                err = ctypes.get_last_error()
                # 87 INVALID_PARAMETER / 1181 JOURNAL_ENTRY_DELETED for a non-boundary start -> from 0
                if err in (87, 1181) and not retried_from_zero and start != 0:
                    start = 0
                    retried_from_zero = True
                    continue
                raise OSError(f"READ_USN_JOURNAL failed (err {err})")
            n = br.value
            if n <= 8:
                break
            data = outbuf.raw[:n]
            next_start = struct.unpack_from("<q", data, 0)[0]
            off = 8
            while off + 8 <= n:
                (rl, maj, mnr) = struct.unpack_from("<IHH", data, off)
                # fixed-header size is version-specific: 60 for V2 (64-bit refs), 76 for V3 (128-bit)
                minlen = 76 if maj == 3 else 60
                if rl < minlen or off + rl > n or off + minlen > n:
                    break
                is_v3 = maj == 3
                try:
                    if is_v3:
                        fref = data[off + 8:off + 24]
                        pref = data[off + 24:off + 40]
                        (usn, ts, reason, src, sec, attrs, fnlen, fnoff) = struct.unpack_from("<qqIIIIHH", data, off + 40)
                    else:
                        (fref, pref, usn, ts, reason, src, sec, attrs, fnlen, fnoff) = struct.unpack_from("<QQqqIIIIHH", data, off + 8)
                except struct.error:
                    break
                if 0 < fnoff and fnoff + fnlen <= rl:
                    name = data[off + fnoff:off + fnoff + fnlen].decode("utf-16-le", "replace")
                else:
                    name = None
                when = _ft_to_dt(ts)
                off += rl
                if when is None or when < cutoff:
                    continue
                path = None
                if want_path and name:
                    parent_dir = _resolve_path(h, pref, pcache, is_v3)
                    path = (parent_dir.rstrip("\\") + "\\" + name) if parent_dir else None
                emitted += 1
                yield {"time": when.isoformat(timespec="seconds"), "name": name, "path": path,
                       "reasons": _reason_names(reason), "attributes": attrs,
                       "file_ref": (fref if isinstance(fref, int) else fref.hex()),
                       "parent_ref": (pref if isinstance(pref, int) else pref.hex())}
            scanned += n
            if next_start <= start:
                break
            start = next_start
        else:
            # loop exited via the while-guard: hit the record/byte backstop before the journal end
            if scanned >= _MAX_BYTES and stats is not None:
                stats["scan_truncated"] = True
    finally:
        _k.CloseHandle(h)


def is_volume_dirty(volume):
    """Locale-independent dirty-bit check via FSCTL_IS_VOLUME_DIRTY. Returns True/False or None on error."""
    if not _valid_volume(volume):
        return None
    try:
        h = _open_volume(volume)
    except OSError:
        return None
    try:
        out = ctypes.create_string_buffer(4)
        br = wintypes.DWORD(0)
        if not _k.DeviceIoControl(h, _FSCTL_IS_VOLUME_DIRTY, None, 0, out, 4, byref(br), None):
            return None
        flags = struct.unpack_from("<I", out, 0)[0]
        return bool(flags & 0x00000001)  # VOLUME_IS_DIRTY
    finally:
        _k.CloseHandle(h)


# --- public API ------------------------------------------------------------
def recent_file_changes(minutes=60, path_filter=None, reasons=None, max=200, volume="C:"):
    if not is_admin():
        return {"error": "USN journal read requires admin; start the server elevated.", "is_admin": False}
    if not _valid_volume(volume):
        return {"error": "invalid volume (expected e.g. 'C:')"}
    flt = (path_filter or "").lower()
    want = set(r.upper() for r in reasons) if reasons else None
    stats = {}
    try:
        # aggregate by file: one row per path, union of reasons, most-recent time
        agg = {}
        for rec in _read_records(volume, minutes, want_path=True, stats=stats):
            if want and not (set(rec["reasons"]) & want):
                continue
            key_str = (rec["path"] or rec["name"] or "").lower()
            if flt and flt not in key_str:
                continue
            k = rec["path"] or rec["name"] or f"ref:{rec['file_ref']}"
            cur = agg.get(k)
            if cur is None:
                rec = dict(rec)
                rec["reasons"] = sorted(set(rec["reasons"]))
                agg[k] = rec
            else:
                cur["reasons"] = sorted(set(cur["reasons"]) | set(rec["reasons"]))
                if rec["time"] > cur["time"]:
                    cur["time"] = rec["time"]
    except Exception as e:
        return {"error": str(e)}
    rows = sorted(agg.values(), key=lambda r: r["time"], reverse=True)
    total = len(rows)
    res = {"window_minutes": int(minutes), "volume": volume, "count": min(total, int(max)),
           "total_matching": total, "truncated": total > int(max), "changes": rows[:int(max)]}
    if stats.get("scan_truncated"):
        res["scan_truncated"] = True
        res["scan_note"] = "journal exceeds the scan budget; recent changes may be incomplete"
    return res


def directory_churn(minutes=60, top_n=20, volume="C:"):
    if not is_admin():
        return {"error": "USN journal read requires admin; start the server elevated.", "is_admin": False}
    if not _valid_volume(volume):
        return {"error": "invalid volume (expected e.g. 'C:')"}
    stats = {}
    try:
        dirs = {}
        for rec in _read_records(volume, minutes, want_path=True, stats=stats):
            d = os.path.dirname(rec["path"]) if rec["path"] else "(unresolved)"
            e = dirs.setdefault(d, {"directory": d, "change_count": 0, "sample_files": []})
            e["change_count"] += 1
            if rec["name"] and len(e["sample_files"]) < 5 and rec["name"] not in e["sample_files"]:
                e["sample_files"].append(rec["name"])
    except Exception as e:
        return {"error": str(e)}
    ordered = sorted(dirs.values(), key=lambda x: x["change_count"], reverse=True)
    res = {"window_minutes": int(minutes), "volume": volume, "directory_count": len(dirs),
           "directories": ordered[:int(top_n)]}
    if stats.get("scan_truncated"):
        res["scan_truncated"] = True
    return res


def usn_status(volume="C:"):
    if not _valid_volume(volume):
        return {"error": "invalid volume"}
    if not is_admin():
        return {"error": "requires admin", "is_admin": False}
    try:
        h = _open_volume(volume)
        try:
            j = _query_journal(h)
        finally:
            _k.CloseHandle(h)
        j["span_bytes"] = j["next_usn"] - j["first_usn"]
        return j
    except OSError as e:
        return {"error": str(e)}
