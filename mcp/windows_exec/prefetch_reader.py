"""Prefetch (.pf) reader: MAM/Xpress-Huffman decompress + SCCA v30/31 parse. No MCP deps.

Verified on this box (build 26100, SCCA v31):
- File = 'MAM\\x04' + uint32 uncompressed_size + Xpress-Huffman stream. Decompress with
  RtlGetCompressionWorkSpaceSize + RtlDecompressBufferEx(COMPRESSION_FORMAT_XPRESS_HUFF=0x0004);
  plain RtlDecompressBuffer returns STATUS_UNSUPPORTED_COMPRESSION (0xC00000E8).
- Decompressed: version@0x00, 'SCCA'@0x04, exe name (UTF-16)@0x10, hash@0x4C; file-info @0x54 with
  filename_strings off@0x64/size@0x68, volumes off@0x6C/count@0x70; 8x last-run FILETIME @0x80;
  run_count @0xC8 (v31) / @0xD0 (v30).
All parsing is bounds-checked and caps attacker-influenced lengths (a .pf is OS-written but untrusted).
"""
import ctypes
import datetime as dt
import os
import struct

PREFETCH_DIR = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Prefetch")
_COMPRESSION_FORMAT_XPRESS_HUFF = 0x0004
_MAX_UNCOMPRESSED = 32 * 1024 * 1024   # 32 MB cap on a decompressed .pf
_MAX_FILES = 2000                       # loaded-file list cap
_FILETIME_MAX = 2_650_467_744_000_000_000

_ntdll = ctypes.windll.ntdll


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def prefetch_enabled():
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r"SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management\PrefetchParameters")
        v, _ = winreg.QueryValueEx(k, "EnablePrefetcher")
        return int(v)
    except Exception:
        return None


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


def _decompress(raw):
    """MAM Xpress-Huffman -> decompressed bytes, or raise ValueError."""
    if len(raw) < 8 or raw[:4] != b"MAM\x04":
        raise ValueError("not a MAM-compressed prefetch file")
    (usize,) = struct.unpack_from("<I", raw, 4)
    if usize <= 0 or usize > _MAX_UNCOMPRESSED:
        raise ValueError(f"implausible uncompressed size {usize}")
    comp = raw[8:]
    bufw = ctypes.c_ulong(0)
    fragw = ctypes.c_ulong(0)
    st = _ntdll.RtlGetCompressionWorkSpaceSize(ctypes.c_ushort(_COMPRESSION_FORMAT_XPRESS_HUFF),
                                               ctypes.byref(bufw), ctypes.byref(fragw))
    if st & 0xFFFFFFFF != 0:
        raise ValueError(f"RtlGetCompressionWorkSpaceSize failed 0x{st & 0xFFFFFFFF:X}")
    ws = ctypes.create_string_buffer(bufw.value)
    out = ctypes.create_string_buffer(usize)
    final = ctypes.c_ulong(0)
    st = _ntdll.RtlDecompressBufferEx(ctypes.c_ushort(_COMPRESSION_FORMAT_XPRESS_HUFF),
                                      out, usize, comp, len(comp), ctypes.byref(final), ws)
    if st & 0xFFFFFFFF != 0:
        raise ValueError(f"RtlDecompressBufferEx failed 0x{st & 0xFFFFFFFF:X}")
    return out.raw[:final.value]


def _u32(buf, off):
    return struct.unpack_from("<I", buf, off)[0]


def _parse_scca(d, want_files=False):
    if len(d) < 0x100 or d[4:8] != b"SCCA":
        raise ValueError("not an SCCA prefetch stream")
    version = _u32(d, 0)
    exe = d[0x10:0x4C].decode("utf-16-le", "replace").split("\x00")[0]
    prefetch_hash = f"0x{_u32(d, 0x4C):08X}"
    # last-run times: 8 FILETIMEs at 0x80
    run_times = []
    for i in range(8):
        off = 0x80 + i * 8
        (ft,) = struct.unpack_from("<Q", d, off)
        iso = _ft_to_iso(ft)
        if iso:
            run_times.append(iso)
    # run count: v31 @0xC8, v30 @0xD0
    rc_off = 0xC8 if version >= 31 else 0xD0
    run_count = _u32(d, rc_off)
    if not (0 <= run_count <= 10_000_000):
        run_count = None
    rec = {"exe": exe, "version": version, "prefetch_hash": prefetch_hash,
           "run_count": run_count, "run_times": run_times}
    if not run_times:
        rec["run_time_uncertain"] = True
    # volume device path (best-effort)
    try:
        vol_off = _u32(d, 0x6C)
        vcount = _u32(d, 0x70)
        if 0 < vcount < 1000 and 0 < vol_off < len(d) - 0x18:
            vdev_off = _u32(d, vol_off)
            vdev_len = _u32(d, vol_off + 4)
            vserial = _u32(d, vol_off + 0x10)
            start = vol_off + vdev_off
            end = start + vdev_len * 2
            if 0 < vdev_len < 1000 and end <= len(d):
                rec["volume"] = {"device": d[start:end].decode("utf-16-le", "replace").split("\x00")[0],
                                 "serial": f"0x{vserial:08X}"}
    except (struct.error, ValueError):
        pass
    # loaded-file list (optional; large)
    if want_files:
        try:
            fn_off = _u32(d, 0x64)
            fn_size = _u32(d, 0x68)
            if 0 < fn_off < len(d) and 0 < fn_size <= len(d) - fn_off:
                blob = d[fn_off:fn_off + fn_size].decode("utf-16-le", "replace")
                files = [s for s in blob.split("\x00") if s]
                rec["file_count"] = len(files)
                rec["files"] = files[:_MAX_FILES]
                if len(files) > _MAX_FILES:
                    rec["files_truncated"] = True
        except (struct.error, ValueError):
            pass
    return rec


def _safe_pf_name(name):
    """Confine to a .pf basename inside PREFETCH_DIR."""
    if not name or name in (".", ".."):
        return None
    if os.path.basename(name) != name or "/" in name or "\\" in name:
        return None
    if not name.lower().endswith(".pf"):
        name += ".pf"
    return name


def read_pf(path, want_files=False):
    with open(path, "rb") as fh:
        raw = fh.read()
    d = _decompress(raw)
    rec = _parse_scca(d, want_files=want_files)
    rec["pf_file"] = os.path.basename(path)
    return rec


# --- public API ------------------------------------------------------------
def prefetch_list(filter=None, max=50):
    if not is_admin():
        return {"error": "Prefetch read requires admin; start the server elevated.", "is_admin": False}
    if not os.path.isdir(PREFETCH_DIR):
        return {"error": "no Prefetch directory", "path": PREFETCH_DIR}
    flt = (filter or "").lower()
    try:
        entries = [e for e in os.scandir(PREFETCH_DIR)
                   if e.is_file() and e.name.lower().endswith(".pf")]
    except (PermissionError, OSError) as e:
        return {"error": str(e)}
    entries.sort(key=lambda e: e.stat().st_mtime, reverse=True)
    rows = []
    scanned = 0
    for e in entries:
        if flt and flt not in e.name.lower():
            continue
        scanned += 1
        try:
            r = read_pf(e.path, want_files=False)
            rows.append({"exe": r["exe"], "run_count": r["run_count"],
                         "last_run": r["run_times"][0] if r["run_times"] else None,
                         "hash": r["prefetch_hash"], "pf_file": r["pf_file"]})
        except Exception as ex:
            rows.append({"pf_file": e.name, "parse_error": str(ex)})
        if len(rows) >= int(max):
            break
    total = sum(1 for e in entries if not flt or flt in e.name.lower())
    return {"count": len(rows), "total_matching": total, "truncated": total > len(rows),
            "prefetch_enabled": prefetch_enabled(), "prefetch": rows}


def prefetch_detail(name):
    if not is_admin():
        return {"error": "Prefetch read requires admin; start the server elevated.", "is_admin": False}
    safe = _safe_pf_name(name)
    if safe is None:
        return {"error": "invalid prefetch name (basename only, must be a .pf file)"}
    path = os.path.join(PREFETCH_DIR, safe)
    if not os.path.isfile(path):
        return {"error": "prefetch file not found", "name": safe}
    try:
        return read_pf(path, want_files=True)
    except Exception as e:
        return {"error": str(e), "name": safe}


def iter_prefetch_runs():
    """Yield (exe, iso_time, run_count) for every recorded run time — for the timeline."""
    if not is_admin() or not os.path.isdir(PREFETCH_DIR):
        return
    try:
        entries = [e for e in os.scandir(PREFETCH_DIR)
                   if e.is_file() and e.name.lower().endswith(".pf")]
    except (PermissionError, OSError):
        return
    for e in entries:
        try:
            r = read_pf(e.path, want_files=False)
        except Exception:
            continue
        for t in r.get("run_times", []):
            yield (r["exe"], t, r.get("run_count"))
