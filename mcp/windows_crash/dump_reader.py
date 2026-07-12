"""Crash-dump reader: enumerate + struct-parse kernel/user dumps. No MCP deps.

Two dump families:
- Kernel (BSOD) minidumps: magic 'PAGEDU64' (64-bit) / 'PAGEDUMP' (32-bit), a DUMP_HEADER whose
  BugCheckCode + 4 params + build we struct-parse. Verified on this box (build 26100).
- User-mode: magic 'MDMP' (MINIDUMP_HEADER) — walk the stream directory for the exception code/address
  and the loaded-module list. Pure struct via a read-only mmap (bounded; never reads the whole file).

`cdb.exe` (Windows SDK Debuggers) is OPTIONAL: if present, analyze_dump can run `!analyze -v` for a
symbol-resolved verdict; if absent, the header parse still answers "which bugcheck, which build".

Security: reached over loopback by a local agent, runs elevated, parses UNTRUSTED bytes. So:
- analyze_dump resolves the path ONCE (realpath) and operates on that canonical path (no check-vs-use
  gap), confined to the OS dump dirs + the WER store; read-only, never writes/deletes.
- the MDMP parser is fully bounds-checked and caps every attacker-controlled length/count.
"""
import ctypes
import datetime as dt
import mmap
import os
import platform
import re
import struct

import bugchecks

WINDIR = os.environ.get("SystemRoot", r"C:\Windows")
MINIDUMP_DIR = os.path.join(WINDIR, "Minidump")
MEMORY_DMP = os.path.join(WINDIR, "MEMORY.DMP")
LIVEKERNEL_DIR = os.path.join(WINDIR, "LiveKernelReports")

_MACHINE = {0x014C: "x86", 0x8664: "x64", 0xAA64: "ARM64", 0x0200: "IA64"}

# MDMP hardening caps (attacker-controlled fields must never drive unbounded work).
_MDMP_NAME_MAX = 4096        # bytes per module name (MAX_PATH*2 = 520; 4 KB is generous)
_MDMP_MAX_STREAMS = 128
_MDMP_MAX_MODULES = 512      # module records scanned
_MDMP_RETURN_MODULES = 60    # module names returned

_CDB_CACHE = {}  # path+mtime -> analyze_v dict (only successful runs cached)
_CDB_CACHE_MAX = 256
_CDB_PATH = None
_CDB_PROBED = False


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# --- enumeration -----------------------------------------------------------
def _entry(path):
    try:
        st = os.stat(path)
        return {"path": path, "size_mb": round(st.st_size / 1048576, 2),
                "modified": dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc).isoformat(timespec="seconds")}
    except OSError as e:
        return {"path": path, "error": str(e)}


def list_dumps():
    out = {"minidumps": [], "memory_dmp": None, "livekernel": []}
    if os.path.isdir(MINIDUMP_DIR):
        try:
            for e in os.scandir(MINIDUMP_DIR):
                if e.is_file() and e.name.lower().endswith(".dmp"):
                    out["minidumps"].append(_entry(e.path))
        except (PermissionError, OSError) as e:
            out["minidumps_error"] = str(e)
    out["minidumps"].sort(key=lambda d: d.get("modified") or "", reverse=True)
    if os.path.isfile(MEMORY_DMP):
        out["memory_dmp"] = _entry(MEMORY_DMP)
    if os.path.isdir(LIVEKERNEL_DIR):
        try:
            for root, _dirs, files in os.walk(LIVEKERNEL_DIR):
                for name in files:
                    if name.lower().endswith(".dmp"):
                        out["livekernel"].append(_entry(os.path.join(root, name)))
        except (PermissionError, OSError) as e:
            out["livekernel_error"] = str(e)
    out["livekernel"].sort(key=lambda d: d.get("modified") or "", reverse=True)
    out["counts"] = {"minidumps": len(out["minidumps"]),
                     "memory_dmp": 1 if out["memory_dmp"] else 0,
                     "livekernel": len(out["livekernel"])}
    return out


# --- path safety -----------------------------------------------------------
def _allowed_roots():
    roots = [MINIDUMP_DIR, LIVEKERNEL_DIR]
    pd = os.environ.get("ProgramData", r"C:\ProgramData")
    roots.append(os.path.join(pd, "Microsoft", "Windows", "WER"))
    la = os.environ.get("LOCALAPPDATA", "")
    if la:
        roots.append(os.path.join(la, "Microsoft", "Windows", "WER"))
    return [os.path.normcase(os.path.realpath(r)) for r in roots]


def _resolve_allowed(path):
    """Return the canonical (realpath) form if it is inside an allowed root, else None.

    Callers MUST open/stat the returned canonical path (not the original argument) so the validated
    path and the used path are identical — closing the check-vs-use gap. Junction/symlink components
    are resolved by realpath at this point.
    """
    if not path:
        return None
    try:
        rp = os.path.realpath(path)
    except OSError:
        return None
    rpn = os.path.normcase(rp)
    try:
        if rpn == os.path.normcase(os.path.realpath(MEMORY_DMP)):
            return rp
    except OSError:
        pass
    for root in _allowed_roots():
        if rpn == root or rpn.startswith(root + os.sep):
            return rp
    return None


# --- kernel dump (DUMP_HEADER) --------------------------------------------
def _parse_kernel_header(buf, bits64):
    if bits64:  # DUMP_HEADER64: pointers are ULONG64
        major, minor = struct.unpack_from("<II", buf, 0x08)
        machine, nproc = struct.unpack_from("<II", buf, 0x30)
        (bugcheck,) = struct.unpack_from("<I", buf, 0x38)
        params = struct.unpack_from("<QQQQ", buf, 0x40)
    else:       # DUMP_HEADER32: pointers are ULONG, so the tail shifts up by 0x10
        major, minor = struct.unpack_from("<II", buf, 0x08)
        machine, nproc = struct.unpack_from("<II", buf, 0x20)
        (bugcheck,) = struct.unpack_from("<I", buf, 0x28)
        params = struct.unpack_from("<IIII", buf, 0x2C)
    name, desc = bugchecks.describe_bugcheck(bugcheck)
    layout_ok = 1000 <= minor <= 99999  # build should look like a real Windows build number
    rec = {
        "kind": "kernel",
        "bugcheck_code": f"0x{bugcheck:08X}",
        "bugcheck_name": name,
        "bugcheck_desc": desc,
        "parameters": [f"0x{p:016X}" for p in params],
        "build": minor,
        "major_version": major,
        "machine": _MACHINE.get(machine, f"0x{machine:04X}"),
        "processors": nproc,
    }
    if not layout_ok:
        rec["layout_uncertain"] = True
    return rec


# --- user dump (MDMP) ------------------------------------------------------
_STREAM_MODULE_LIST = 4
_STREAM_EXCEPTION = 6
_STREAM_SYSTEM_INFO = 7


def _read_minidump_string(mm, rva, size):
    """Bounded MINIDUMP_STRING read: u32 byte-length + UTF-16 chars, every field range-checked."""
    if not rva or rva < 0 or rva + 4 > size:
        return None
    try:
        (length,) = struct.unpack_from("<I", mm, rva)
    except struct.error:
        return None
    length = min(length, _MDMP_NAME_MAX)
    if rva + 4 + length > size:
        length = size - (rva + 4)
    if length <= 0:
        return None
    try:
        return bytes(mm[rva + 4: rva + 4 + length]).decode("utf-16-le", "replace")
    except Exception:
        return None


def _parse_user_dump(mm, size):
    (sig, _ver, nstreams, dir_rva) = struct.unpack_from("<IIII", mm, 0)
    rec = {"kind": "user", "streams": nstreams}
    try:
        (ts,) = struct.unpack_from("<I", mm, 0x14)
        if ts:
            rec["dump_time"] = dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds")
    except (struct.error, OSError, ValueError, OverflowError):
        pass
    if nstreams > _MDMP_MAX_STREAMS:
        rec["streams_truncated"] = True
    streams = {}
    for i in range(min(nstreams, _MDMP_MAX_STREAMS)):
        off = dir_rva + i * 12
        try:
            stype, dsize, rva = struct.unpack_from("<III", mm, off)
        except struct.error:
            break
        streams[stype] = (dsize, rva)
    # exception
    if _STREAM_EXCEPTION in streams:
        _dsize, rva = streams[_STREAM_EXCEPTION]
        try:
            (code,) = struct.unpack_from("<I", mm, rva + 8)          # MINIDUMP_EXCEPTION.ExceptionCode
            (addr,) = struct.unpack_from("<Q", mm, rva + 8 + 16)     # .ExceptionAddress
            hexcode = f"{code:08x}"
            name, desc = bugchecks.describe_exception(hexcode)
            rec["exception_code"] = f"0x{code:08X}"
            rec["code_meaning"] = name
            rec["code_desc"] = desc
            rec["exception_address"] = f"0x{addr:016X}"
        except struct.error:
            pass
    # module list
    if _STREAM_MODULE_LIST in streams:
        _dsize, rva = streams[_STREAM_MODULE_LIST]
        try:
            (nmods,) = struct.unpack_from("<I", mm, rva)
        except struct.error:
            nmods = None
        if nmods is not None:
            rec["module_count"] = nmods
            mods = []
            for i in range(min(nmods, _MDMP_MAX_MODULES)):
                base = rva + 4 + i * 108
                # MINIDUMP_MODULE: BaseOfImage@0, SizeOfImage@8, CheckSum@12, TimeDateStamp@16,
                # ModuleNameRva@20, VersionInfo(VS_FIXEDFILEINFO)@24 ...
                try:
                    name_rva = struct.unpack_from("<I", mm, base + 20)[0]
                except struct.error:
                    break  # record runs past the buffer; keep what we have
                nm = _read_minidump_string(mm, name_rva, size)
                if nm:
                    mods.append(os.path.basename(nm.replace("\\", "/")))
                if len(mods) >= _MDMP_RETURN_MODULES:
                    break
            rec["modules"] = mods
            if nmods > len(mods):
                rec["modules_truncated"] = True
    return rec


def _parse_user_dump_file(path):
    try:
        with open(path, "rb") as fh:
            size = os.fstat(fh.fileno()).st_size
            if size < 32:
                return {"error": "file too short to be an MDMP dump", "path": path}
            with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                return _parse_user_dump(mm, size)
    except PermissionError:
        return {"error": "permission denied (start the server elevated to read this dump)",
                "path": path, "is_admin": is_admin()}
    except (ValueError, OSError) as e:
        return {"error": str(e), "path": path}
    except struct.error as e:
        return {"error": f"malformed MDMP: {e}", "path": path}


# --- cdb (optional) --------------------------------------------------------
# The Debugging Tools ship one cdb.exe per architecture (Debuggers\x64, \arm64, \x86). Probe only the
# ones this process can actually execute, native first. platform.machine() reports the *process*
# architecture, which is exactly the right predicate: an x64 Python emulated on an ARM64 host says
# AMD64 and should launch the (also emulated) x64 cdb. Probing every arch dir instead would let an x64
# box pick up a cross-installed arm64 cdb.exe it cannot run.
_CDB_ARCH_DIRS = {"ARM64": ("arm64", "x64"), "AMD64": ("x64",), "X86": ("x86",)}


def _cdb_candidates():
    arch_dirs = _CDB_ARCH_DIRS.get(platform.machine().upper(), ("x64",))
    candidates = []
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if base:
            for kit in ("10", "11"):
                for arch in arch_dirs:
                    candidates.append(os.path.join(base, "Windows Kits", kit, "Debuggers", arch, "cdb.exe"))
    la = os.environ.get("LOCALAPPDATA")
    if la:
        candidates.append(os.path.join(la, "Microsoft", "WindowsApps", "cdb.exe"))
    return candidates


def _find_cdb():
    global _CDB_PATH, _CDB_PROBED
    if _CDB_PROBED:
        return _CDB_PATH
    _CDB_PROBED = True
    for c in _cdb_candidates():
        if c and os.path.isfile(c):
            _CDB_PATH = c
            return c
    return None


def cdb_available():
    return _find_cdb() is not None


def _run_cdb(path):
    import subprocess
    key = None
    try:
        key = f"{path}:{os.path.getmtime(path)}"
    except OSError:
        pass
    if key and key in _CDB_CACHE:
        return _CDB_CACHE[key]
    cdb = _find_cdb()
    if not cdb:
        return {"cdb_available": False,
                "hint": "Install Windows SDK 'Debugging Tools for Windows' (winget install Microsoft.WinDbg) to enable !analyze -v."}
    env = dict(os.environ)
    env.setdefault("_NT_SYMBOL_PATH", "srv*C:\\symbols*https://msdl.microsoft.com/download/symbols")
    try:
        r = subprocess.run([cdb, "-z", path, "-c", "!analyze -v; q"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=env, timeout=300)
    except subprocess.TimeoutExpired:
        return {"cdb_available": True, "error": "cdb timed out (symbol download may be slow)"}
    except Exception as e:
        return {"cdb_available": True, "error": str(e)}
    out = r.stdout or ""

    def grab(pat):
        m = re.search(pat, out, re.IGNORECASE)
        return m.group(1).strip() if m else None

    stack = []
    m = re.search(r"STACK_TEXT:\s*\n(.*?)\n\s*\n", out, re.DOTALL)
    if m:
        lines = [ln.strip() for ln in m.group(1).splitlines() if ln.strip()]
        stack = lines[:20]
        stack_truncated = len(lines) > 20
    else:
        stack_truncated = False
    verdict = {
        "cdb_available": True,
        "probably_caused_by": grab(r"PROBABLY_CAUSED_BY:\s*(.+)"),
        "failure_bucket": grab(r"FAILURE_BUCKET_ID:\s*(.+)"),
        "image_name": grab(r"IMAGE_NAME:\s*(.+)"),
        "module_name": grab(r"MODULE_NAME:\s*(.+)"),
        "bugcheck_str": grab(r"BugCheck [0-9A-Fa-f]+, \{(.+?)\}"),
        "stack": stack,
        "stack_truncated": stack_truncated,
    }
    extracted = any(verdict[k] for k in
                    ("probably_caused_by", "failure_bucket", "image_name", "module_name", "bugcheck_str", "stack"))
    if r.returncode != 0 and not extracted:
        # transient/failed run: do NOT cache, so a later retry can succeed
        return {"cdb_available": True, "error": f"cdb exited {r.returncode} with no analyzable output",
                "returncode": r.returncode}
    if key:
        if len(_CDB_CACHE) >= _CDB_CACHE_MAX:
            _CDB_CACHE.clear()
        _CDB_CACHE[key] = verdict
    return verdict


# --- public API ------------------------------------------------------------
def analyze_dump(path, use_cdb=False):
    canonical = _resolve_allowed(path)
    if canonical is None:
        return {"error": "path not allowed: must be under the OS dump dirs (Minidump / LiveKernelReports / MEMORY.DMP) or the WER store"}
    path = canonical  # operate ONLY on the validated canonical path
    if not os.path.isfile(path):
        return {"error": "dump not found", "path": path}
    try:
        with open(path, "rb") as fh:
            head = fh.read(0x60)
    except PermissionError:
        return {"error": "permission denied (start the server elevated to read this dump)", "path": path,
                "is_admin": is_admin()}
    except OSError as e:
        return {"error": str(e), "path": path}
    if len(head) < 8:
        return {"error": "file too short to be a dump", "path": path}
    magic8, magic4 = head[:8], head[:4]
    try:
        if magic8 == b"PAGEDU64":
            if len(head) < 0x60:
                return {"error": "kernel dump header truncated", "path": path}
            rec = _parse_kernel_header(head, bits64=True)
        elif magic8 == b"PAGEDUMP":
            if len(head) < 0x60:
                return {"error": "kernel dump header truncated", "path": path}
            rec = _parse_kernel_header(head, bits64=False)
        elif magic4 == b"MDMP":
            rec = _parse_user_dump_file(path)  # bounded mmap parse
        else:
            return {"error": "unrecognized dump magic",
                    "magic": magic8.decode("latin-1", "replace"), "path": path}
    except Exception as e:
        return {"error": f"parse failed: {e}", "path": path,
                "magic": magic8.decode("latin-1", "replace")}
    if "error" in rec:
        return rec
    rec["path"] = path
    rec["cdb_available"] = cdb_available()
    if use_cdb:
        rec["analyze_v"] = _run_cdb(path)
    return rec


def health():
    try:
        dumps = list_dumps()
        return {"is_admin": is_admin(), "cdb_available": cdb_available(),
                "cdb_path": _find_cdb(),
                "minidump_dir": MINIDUMP_DIR, "counts": dumps.get("counts")}
    except Exception as e:
        return {"is_admin": is_admin(), "error": str(e)}
