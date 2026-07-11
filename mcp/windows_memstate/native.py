"""Kernel memory-state primitives via ctypes: NtQuerySystemInformation pool tags + memory lists, and
psapi GetPerformanceInfo. No MCP deps. Read-only.

Verified this box (x64): SYSTEM_POOLTAG = 40 bytes; SystemPoolTagInformation(0x16) = ULONG Count +
Count x SYSTEM_POOLTAG (array at offset 8); SystemMemoryListInformation(0x50) = ULONG_PTR fields;
GetPerformanceInfo returns pages. STATUS_INFO_LENGTH_MISMATCH -> resize loop.
"""
import ctypes
import struct
from ctypes import wintypes, byref, POINTER, c_size_t, c_uint

_nt = ctypes.WinDLL("ntdll", use_last_error=True)
_ps = ctypes.WinDLL("psapi", use_last_error=True)
_nt.NtQuerySystemInformation.restype = wintypes.LONG
_nt.NtQuerySystemInformation.argtypes = [wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG,
                                         POINTER(wintypes.ULONG)]

_STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
_SystemPoolTagInformation = 0x16
_SystemMemoryListInformation = 0x50
_PAGE = 4096


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


class SYSTEM_POOLTAG(ctypes.Structure):
    _fields_ = [("Tag", ctypes.c_char * 4),
                ("PagedAllocs", c_uint), ("PagedFrees", c_uint), ("PagedUsed", c_size_t),
                ("NonPagedAllocs", c_uint), ("NonPagedFrees", c_uint), ("NonPagedUsed", c_size_t)]


class PERFORMANCE_INFORMATION(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("CommitTotal", c_size_t), ("CommitLimit", c_size_t),
                ("CommitPeak", c_size_t), ("PhysicalTotal", c_size_t), ("PhysicalAvailable", c_size_t),
                ("SystemCache", c_size_t), ("KernelTotal", c_size_t), ("KernelPaged", c_size_t),
                ("KernelNonpaged", c_size_t), ("PageSize", c_size_t), ("HandleCount", wintypes.DWORD),
                ("ProcessCount", wintypes.DWORD), ("ThreadCount", wintypes.DWORD)]


def _query(cls, extra=8192):
    ln = wintypes.ULONG(0)
    _nt.NtQuerySystemInformation(cls, None, 0, byref(ln))
    for _ in range(6):
        size = ln.value + extra
        buf = ctypes.create_string_buffer(size)
        st = _nt.NtQuerySystemInformation(cls, buf, size, byref(ln)) & 0xFFFFFFFF
        if st == 0:
            return buf.raw[:ln.value]
        if st != _STATUS_INFO_LENGTH_MISMATCH:
            raise OSError(f"NtQuerySystemInformation(0x{cls:X}) status 0x{st:08X}")
    raise OSError(f"NtQuerySystemInformation(0x{cls:X}) length loop did not converge")


# --- pool tags -------------------------------------------------------------
def pool_tags_raw():
    """Return list of {tag, paged_used, nonpaged_used, paged_allocs, paged_frees, nonpaged_allocs,
    nonpaged_frees}. Raises OSError on failure."""
    data = _query(_SystemPoolTagInformation)
    if len(data) < 8:
        raise OSError("pool tag buffer too short")
    (count,) = struct.unpack_from("<I", data, 0)
    if count <= 0 or 8 + count * ctypes.sizeof(SYSTEM_POOLTAG) > len(data):
        raise OSError(f"implausible pool tag count {count}")
    arr = (SYSTEM_POOLTAG * count).from_buffer_copy(data, 8)
    out = []
    for t in arr:
        out.append({"tag": t.Tag.decode("latin-1").rstrip("\x00") or t.Tag.hex(),
                    "paged_used": t.PagedUsed, "nonpaged_used": t.NonPagedUsed,
                    "paged_allocs": t.PagedAllocs, "paged_frees": t.PagedFrees,
                    "nonpaged_allocs": t.NonPagedAllocs, "nonpaged_frees": t.NonPagedFrees})
    return out


# --- memory list composition ----------------------------------------------
def memory_list_raw():
    """Physical-memory composition in pages. Raises OSError on failure."""
    data = _query(_SystemMemoryListInformation)
    # 5x ULONG_PTR: Zero, Free, Modified, ModifiedNoWrite, Bad ; then PageCountByPriority[8]
    if len(data) < 5 * 8 + 8 * 8:
        raise OSError("memory list buffer too short")
    zero, free, modified, mod_nowrite, bad = struct.unpack_from("<5Q", data, 0)
    prio = struct.unpack_from("<8Q", data, 40)
    standby = sum(prio)
    return {"zero_pages": zero, "free_pages": free, "modified_pages": modified,
            "modified_nowrite_pages": mod_nowrite, "bad_pages": bad,
            "standby_pages": standby, "standby_by_priority_pages": list(prio), "page_size": _PAGE}


# --- performance overview --------------------------------------------------
def performance_info():
    perf = PERFORMANCE_INFORMATION()
    perf.cb = ctypes.sizeof(PERFORMANCE_INFORMATION)
    if not _ps.GetPerformanceInfo(byref(perf), perf.cb):
        raise OSError("GetPerformanceInfo failed")
    pg = perf.PageSize or _PAGE
    return {"page_size": pg,
            "physical_total_pages": perf.PhysicalTotal, "physical_available_pages": perf.PhysicalAvailable,
            "commit_total_pages": perf.CommitTotal, "commit_limit_pages": perf.CommitLimit,
            "kernel_paged_pages": perf.KernelPaged, "kernel_nonpaged_pages": perf.KernelNonpaged,
            "system_cache_pages": perf.SystemCache,
            "handles": perf.HandleCount, "processes": perf.ProcessCount, "threads": perf.ThreadCount}
