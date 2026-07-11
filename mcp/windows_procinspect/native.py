"""Native process-inspection primitives via ctypes: Restart Manager (who locks a file) + Wait Chain
Traversal (why is a process/thread hung / deadlock). No MCP deps. Read-only (queries only).

Verified on this box: RmGetList returns the holding pid+app for a locked file; GetThreadWaitChain
returns the wait chain (Thread -> lock -> owner) with a deadlock (IsCycle) flag.
"""
import ctypes
from ctypes import wintypes, byref, POINTER, c_void_p

_rm = ctypes.WinDLL("rstrtmgr", use_last_error=True)
_adv = ctypes.WinDLL("advapi32", use_last_error=True)
_k = ctypes.WinDLL("kernel32", use_last_error=True)

# --- Restart Manager -------------------------------------------------------
_CCH_KEY = 32
_CCH_APP = 255
_CCH_SVC = 63
_RM_APP_TYPE = {0: "Unknown", 1: "MainWindow", 2: "OtherWindow", 3: "Service", 4: "Explorer",
                5: "Console", 1000: "Critical"}


class RM_UNIQUE_PROCESS(ctypes.Structure):
    _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", wintypes.FILETIME)]


class RM_PROCESS_INFO(ctypes.Structure):
    _fields_ = [("Process", RM_UNIQUE_PROCESS),
                ("strAppName", wintypes.WCHAR * (_CCH_APP + 1)),
                ("strServiceShortName", wintypes.WCHAR * (_CCH_SVC + 1)),
                ("ApplicationType", wintypes.DWORD), ("AppStatus", wintypes.ULONG),
                ("TSSessionId", wintypes.DWORD), ("bRestartable", wintypes.BOOL)]


# argtypes/restype so return codes are unsigned and ctypes validates args (matches the WCT decls below)
_rm.RmStartSession.restype = wintypes.DWORD
_rm.RmStartSession.argtypes = [POINTER(wintypes.DWORD), wintypes.DWORD, wintypes.LPWSTR]
_rm.RmRegisterResources.restype = wintypes.DWORD
_rm.RmRegisterResources.argtypes = [wintypes.DWORD, wintypes.UINT, POINTER(wintypes.LPCWSTR),
                                    wintypes.UINT, c_void_p, wintypes.UINT, POINTER(wintypes.LPCWSTR)]
_rm.RmGetList.restype = wintypes.DWORD
_rm.RmGetList.argtypes = [wintypes.DWORD, POINTER(wintypes.UINT), POINTER(wintypes.UINT),
                          POINTER(RM_PROCESS_INFO), POINTER(wintypes.DWORD)]
_rm.RmEndSession.restype = wintypes.DWORD
_rm.RmEndSession.argtypes = [wintypes.DWORD]


def who_locks(path):
    """Which processes currently have `path` (file or directory) open, via the Restart Manager API."""
    if not isinstance(path, str) or not path:
        return {"error": "path required"}
    sess = wintypes.DWORD(0)
    key = (ctypes.c_wchar * (_CCH_KEY + 1))()
    if _rm.RmStartSession(byref(sess), 0, key) != 0:
        return {"error": "RmStartSession failed"}
    try:
        arr = (ctypes.c_wchar_p * 1)(path)
        rc = _rm.RmRegisterResources(sess, 1, arr, 0, None, 0, None)
        if rc != 0:
            return {"error": f"RmRegisterResources failed (rc {rc}); path may not exist"}
        need = wintypes.UINT(0)
        cnt = wintypes.UINT(0)
        reason = wintypes.DWORD(0)
        _rm.RmGetList(sess, byref(need), byref(cnt), None, byref(reason))  # sizing probe
        n = max(need.value, 1)
        infos = (RM_PROCESS_INFO * n)()
        cnt = wintypes.UINT(n)
        rc = _rm.RmGetList(sess, byref(need), byref(cnt), infos, byref(reason))
        if rc != 0:
            return {"error": f"RmGetList failed (rc {rc})"}
        holders = []
        for i in range(cnt.value):
            p = infos[i]
            holders.append({"pid": p.Process.dwProcessId, "app": p.strAppName,
                            "service": p.strServiceShortName or None,
                            "type": _RM_APP_TYPE.get(p.ApplicationType, p.ApplicationType),
                            "restartable": bool(p.bRestartable)})
        return {"path": path, "count": len(holders), "holders": holders}
    finally:
        _rm.RmEndSession(sess)


# --- Wait Chain Traversal --------------------------------------------------
_WCT_MAX_NODE_COUNT = 16
_WCTP_GETINFO_ALL_FLAGS = 7
_WCT_OBJTYPE = {1: "CriticalSection", 2: "SendMessage", 3: "Mutex", 4: "Alpc", 5: "Com",
                6: "ThreadWait", 7: "ProcWait", 8: "Thread", 9: "ComActivation", 10: "Unknown",
                11: "Socket"}
_WCT_STATUS = {0: "NoAccess", 1: "Running", 2: "Blocked", 3: "PidOnly", 4: "PidOnlyRpcss",
               5: "Owned", 6: "NotOwned", 7: "Abandoned", 8: "Unknown", 9: "Error"}


class _WCT_LOCK(ctypes.Structure):
    _fields_ = [("ObjectName", wintypes.WCHAR * 128), ("Timeout", ctypes.c_longlong),
                ("Alertable", wintypes.BOOL)]


class _WCT_THREAD(ctypes.Structure):
    _fields_ = [("ProcessId", wintypes.DWORD), ("ThreadId", wintypes.DWORD),
                ("WaitTime", wintypes.DWORD), ("ContextSwitches", wintypes.DWORD)]


class _WCT_UNION(ctypes.Union):
    _fields_ = [("LockObject", _WCT_LOCK), ("ThreadObject", _WCT_THREAD)]


class WAITCHAIN_NODE_INFO(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("ObjectType", wintypes.DWORD), ("ObjectStatus", wintypes.DWORD), ("u", _WCT_UNION)]


_adv.OpenThreadWaitChainSession.restype = wintypes.HANDLE
_adv.OpenThreadWaitChainSession.argtypes = [wintypes.DWORD, ctypes.c_void_p]
_adv.CloseThreadWaitChainSession.restype = None
_adv.CloseThreadWaitChainSession.argtypes = [wintypes.HANDLE]
_adv.GetThreadWaitChain.restype = wintypes.BOOL
_adv.GetThreadWaitChain.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                    POINTER(wintypes.DWORD), POINTER(WAITCHAIN_NODE_INFO),
                                    POINTER(wintypes.BOOL)]


def _chain_for_thread(hsession, tid):
    nodes = (WAITCHAIN_NODE_INFO * _WCT_MAX_NODE_COUNT)()
    cnt = wintypes.DWORD(_WCT_MAX_NODE_COUNT)
    iscycle = wintypes.BOOL(0)
    ok = _adv.GetThreadWaitChain(hsession, None, _WCTP_GETINFO_ALL_FLAGS, tid,
                                 byref(cnt), nodes, byref(iscycle))
    if not ok:
        return None, ctypes.get_last_error()  # (chain, err) -- query failed (e.g. access denied)
    chain = []
    for i in range(cnt.value):
        nd = nodes[i]
        otype = _WCT_OBJTYPE.get(nd.ObjectType, nd.ObjectType)
        status = _WCT_STATUS.get(nd.ObjectStatus, nd.ObjectStatus)
        if nd.ObjectType == 8:  # Thread
            chain.append({"type": "Thread", "status": status,
                          "pid": nd.ThreadObject.ProcessId, "tid": nd.ThreadObject.ThreadId,
                          "wait_ms": nd.ThreadObject.WaitTime})
        else:
            chain.append({"type": otype, "status": status,
                          "name": nd.LockObject.ObjectName or None})
    return {"tid": tid, "is_deadlock": bool(iscycle), "nodes": chain}, 0


def wait_chain(pid=None, tid=None):
    """Why is a thread/process hung: the wait chain (Thread -> lock -> owning thread), + deadlock flag."""
    import psutil
    hsession = _adv.OpenThreadWaitChainSession(0, None)
    if not hsession:
        return {"error": "OpenThreadWaitChainSession failed"}
    try:
        tids = []
        if tid is not None:
            tids = [int(tid)]
        elif pid is not None:
            try:
                tids = [t.id for t in psutil.Process(int(pid)).threads()]
            except psutil.Error as e:
                return {"error": f"process {pid}: {e}"}
        else:
            return {"error": "pid or tid required"}
        chains = []
        blocked_only = 0    # threads blocked on an unresolvable single wait (usually just idle)
        unqueryable = 0     # threads GetThreadWaitChain could not query (access denied / >16 nodes)
        for t in tids:
            c, err = _chain_for_thread(hsession, t)
            if c is None:
                unqueryable += 1
                continue
            # a real dependency / deadlock has >1 node (Thread -> lock -> owner) or a cycle;
            # a lone Blocked thread node is usually just an idle worker in a wait
            if len(c["nodes"]) > 1 or c["is_deadlock"]:
                chains.append(c)
            elif c["nodes"] and c["nodes"][0].get("status") == "Blocked":
                blocked_only += 1
        deadlock = any(c["is_deadlock"] for c in chains)
        res = {"pid": pid, "tid": tid, "thread_count": len(tids),
               "blocked_chains": chains, "deadlock_detected": deadlock,
               "idle_blocked_threads": blocked_only, "unqueryable_threads": unqueryable}
        if unqueryable:
            res["note"] = ("some threads could not be queried (try elevated / SeDebugPrivilege); "
                           "deadlock detection may be incomplete")
        return res
    finally:
        _adv.CloseThreadWaitChainSession(hsession)
