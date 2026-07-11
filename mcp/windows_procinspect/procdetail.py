"""psutil-based process detail: deep info, loaded modules (+ optional signature check), handle-leak
candidates. No MCP deps. Read-only.
"""
import ctypes
import datetime as dt
import json
import os
import subprocess

import psutil

_PS = ["powershell", "-NoProfile", "-NonInteractive", "-Command"]


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _iso(ts):
    try:
        return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError, TypeError):
        return None


def process_detail(pid):
    try:
        p = psutil.Process(int(pid))
    except (psutil.NoSuchProcess, ValueError):
        return {"error": f"no such process {pid}"}
    except psutil.Error as e:
        return {"error": str(e)}
    d = {"pid": int(pid)}
    with p.oneshot():
        for key, fn in (("name", p.name), ("exe", p.exe), ("cmdline", lambda: " ".join(p.cmdline())),
                        ("status", p.status), ("username", p.username), ("ppid", p.ppid),
                        ("num_threads", p.num_threads), ("num_handles", getattr(p, "num_handles", lambda: None))):
            try:
                d[key] = fn()
            except (psutil.Error, OSError):
                d[key] = None
        try:
            d["create_time"] = _iso(p.create_time())
        except Exception:
            d["create_time"] = None
        try:
            d["parent_name"] = psutil.Process(d["ppid"]).name() if d.get("ppid") else None
        except Exception:
            d["parent_name"] = None
        try:
            m = p.memory_info()
            d["memory"] = {"rss_mb": round(m.rss / 1048576, 1), "vms_mb": round(m.vms / 1048576, 1)}
        except Exception:
            d["memory"] = None
        try:
            d["cpu_percent"] = p.cpu_percent(interval=0.1)
        except Exception:
            d["cpu_percent"] = None
        for key, fn in (("open_files", lambda: len(p.open_files())),
                        ("connections", lambda: len(p.net_connections(kind="inet"))),
                        ("ctx_switches", lambda: p.num_ctx_switches().voluntary + p.num_ctx_switches().involuntary)):
            try:
                d[key] = fn()
            except Exception:
                d[key] = None
    return d


def loaded_modules(pid, filter=None, check_signatures=False, max=300):
    try:
        p = psutil.Process(int(pid))
        maps = p.memory_maps()
    except (psutil.NoSuchProcess, ValueError):
        return {"error": f"no such process {pid}"}
    except psutil.AccessDenied:
        return {"error": f"access denied to process {pid} (try elevated)"}
    except psutil.Error as e:
        return {"error": str(e)}
    flt = (filter or "").lower()
    paths = []
    seen = set()
    for m in maps:
        path = getattr(m, "path", None)
        if not path or path in seen:
            continue
        if flt and flt not in path.lower():
            continue
        seen.add(path)
        paths.append(path)
    total = len(paths)
    paths = paths[:int(max)]
    mods = [{"path": pth, "name": os.path.basename(pth)} for pth in paths]
    res = {"pid": int(pid), "count": len(mods), "total": total, "truncated": total > len(mods),
           "modules": mods}
    if check_signatures and mods:
        sigs = _check_signatures([m["path"] for m in mods])
        if sigs is None:
            # the Authenticode check itself failed -> do NOT imply everything is trusted
            res["signature_check"] = "unavailable (Get-AuthenticodeSignature failed/unavailable)"
        else:
            for m in mods:
                # a module missing from the result map is 'Unknown', NOT trusted
                m["signature"] = sigs.get(m["path"].lower(), "Unknown")
            # flag anything not positively Valid (includes NotSigned / Unknown / HashMismatch)
            res["untrusted_or_unknown"] = [m["name"] for m in mods if m.get("signature") != "Valid"]
    return res


def _check_signatures(paths):
    """Batch Authenticode check -> {path_lower: status}, or None if the check itself could not run.
    Chunks paths to stay under the command-line length limit."""
    result = {}
    ran_any = False
    for i in range(0, len(paths), 40):
        chunk = paths[i:i + 40]
        arr = ",".join("'" + p.replace("'", "''") + "'" for p in chunk)
        cmd = (f"Get-AuthenticodeSignature -FilePath {arr} | "
               "Select-Object Path,@{n='Status';e={[string]$_.Status}} | ConvertTo-Json -Compress")
        try:
            out = subprocess.run(_PS + [cmd], capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=60).stdout.strip()
        except Exception:
            continue
        ran_any = True
        if not out:
            continue
        try:
            data = json.loads(out)
        except ValueError:
            continue
        if isinstance(data, dict):
            data = [data]
        for d in data:
            if isinstance(d, dict) and d.get("Path"):
                result[d["Path"].lower()] = d.get("Status")
    return result if ran_any else None


def top_handle_users(n=15):
    rows = []
    for p in psutil.process_iter(["pid", "name", "num_handles", "num_threads"]):
        try:
            info = p.info
            h = info.get("num_handles")
            if h is None:
                continue
            rows.append({"pid": info["pid"], "name": info.get("name"),
                         "handles": h, "threads": info.get("num_threads")})
        except psutil.Error:
            continue
    rows.sort(key=lambda r: r["handles"], reverse=True)
    return {"count": min(len(rows), int(n)), "total_processes": len(rows),
            "top_by_handles": rows[:int(n)]}


def find_process(name, max=50):
    flt = (name or "").lower()
    rows = []
    for p in psutil.process_iter(["pid", "name", "num_handles", "num_threads", "username"]):
        try:
            info = p.info
            if flt and flt not in (info.get("name") or "").lower():
                continue
            rows.append({"pid": info["pid"], "name": info.get("name"),
                         "handles": info.get("num_handles"), "threads": info.get("num_threads"),
                         "username": info.get("username")})
        except psutil.Error:
            continue
    rows.sort(key=lambda r: r["pid"])
    total = len(rows)
    return {"count": min(total, int(max)), "total_matching": total,
            "truncated": total > int(max), "processes": rows[:int(max)]}


def health():
    return {"is_admin": is_admin(), "psutil_ok": True, "process_count": len(psutil.pids())}
