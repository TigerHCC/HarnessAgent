"""Pool-tag knowledge: a curated map of common Windows pool tags + an on-demand scan of drivers\\*.sys
to attribute an arbitrary tag to its owning driver(s). No deps.
"""
import os
import threading
import time

DRIVERS_DIR = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "drivers")

# Curated subset of the WDK pooltag.txt for the tags most likely to top the nonpaged list.
KNOWN_TAGS = {
    "EtwB": "ETW buffers (Event Tracing for Windows) - high value often = an overly busy trace session",
    "MmSt": "Mm section-object prototype PTEs (mapped-file metadata)",
    "MmCa": "Mm control areas (mapped files)",
    "File": "File objects",
    "Fcb ": "NTFS File Control Blocks",
    "NtfF": "NTFS FCB", "Ntfn": "NTFS non-paged FCB", "NtFs": "NTFS",
    "Thre": "Thread objects", "Proc": "Process objects", "Toke": "Token objects",
    "Even": "Event objects", "Sema": "Semaphore objects", "Muta": "Mutant objects",
    "Sect": "Section objects", "Dire": "Directory objects", "Key ": "Registry key objects",
    "ObDi": "Object directory", "Obtb": "Object handle tables",
    "CM31": "Configuration Manager (registry) cache", "CMVi": "Registry view",
    "smNp": "Store Manager non-paged (memory compression)", "smSt": "Store Manager",
    "Vad ": "Virtual Address Descriptors", "Vadl": "VAD long", "MmVa": "Mm VADs",
    "LSwi": "Initial thread / kernel-stack", "PsJb": "Job objects",
    "Io  ": "I/O manager", "IoNm": "I/O parse names", "Irp ": "I/O request packets",
    "Devi": "Device objects", "Driv": "Driver objects", "Gh05": "GDI / Win32k",
    "Ttfd": "Font driver", "Uspm": "USB", "NDpp": "NDIS", "Ndpo": "NDIS",
    "TcpE": "TCP endpoints", "TcpL": "TCP listeners", "AfdB": "Winsock AFD buffers",
    "ConT": "Container / Host Compute Network (NAT)", "HTTP": "HTTP.sys",
    "Wdmd": "WDM driver", "PcNw": "Power",
}


_scan_cache = {}
_lock = threading.Lock()
_MAX_SYS_BYTES = 64 * 1024 * 1024  # skip absurdly large .sys files


def describe(tag):
    """Known one-line description for a tag, or None."""
    if not tag:
        return None
    # tags are space-padded to 4 chars in pooltag.txt
    return KNOWN_TAGS.get(tag) or KNOWN_TAGS.get(tag.ljust(4)) or KNOWN_TAGS.get(tag.rstrip())


_NOTE = "heuristic: a tag byte-match may appear in more than one driver"


def _result(tag, matches, scanned, cached, max_matches):
    # uniform shape for cached and fresh results
    return {"tag": tag, "description": describe(tag), "drivers": matches[:max_matches],
            "driver_count": len(matches), "scanned_drivers": scanned, "cached": cached, "note": _NOTE}


def tag_driver(tag, max_matches=20):
    """Best-effort: which driver(s) reference this pool tag (heuristic byte scan of drivers\\*.sys)."""
    if not tag or len(tag) > 4:
        return {"error": "tag must be 1-4 chars"}
    key = tag.ljust(4)
    with _lock:
        cached = _scan_cache.get(key)
    if cached is not None:
        matches, scanned = cached
        return _result(tag, matches, scanned, True, max_matches)
    needle = key.encode("latin-1", "replace")
    matches = []
    scanned = 0
    completed = False
    if os.path.isdir(DRIVERS_DIR):
        try:
            entries = [e for e in os.scandir(DRIVERS_DIR) if e.name.lower().endswith(".sys")]
            completed = True   # scandir succeeded; a partial per-file failure below is fine to cache
        except OSError:
            entries = []
        for e in entries:
            try:
                if e.stat().st_size > _MAX_SYS_BYTES:
                    continue
                with open(e.path, "rb") as fh:
                    if needle in fh.read():
                        matches.append(e.name)
                scanned += 1
            except OSError:
                continue
    # only cache a scan that actually enumerated the directory (don't poison the cache on a transient
    # failure, e.g. dir briefly missing or scandir denied)
    if completed:
        with _lock:
            _scan_cache[key] = (matches, scanned)
    return _result(tag, matches, scanned, False, max_matches)
