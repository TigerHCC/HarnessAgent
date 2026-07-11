"""Filter-stack parsers: fltmc minifilters/instances, NDIS bindings, Winsock LSP, altitude classes.
No MCP deps. Read-only (queries only). JSON minifilter baselines.
"""
import ctypes
import datetime as dt
import json
import os
import subprocess
import threading

DRIVERS_DIR = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "drivers")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
BASELINE_PATH = os.environ.get("FILTERSTACK_BASELINES", os.path.join(DATA_DIR, "filterstack_baselines.json"))
_lock = threading.Lock()
_PS = ["powershell", "-NoProfile", "-NonInteractive", "-Command"]

# Microsoft's allocated minifilter altitude ranges (load-order groups) -> class + meaning.
_ALT_RANGES = [
    (420000, 429999, "Filter", "top-of-stack filter"),
    (400000, 409999, "FSFilter Top", "top instance"),
    (360000, 389999, "FSFilter Activity Monitor", "monitoring / EDR / instrumentation (Sysmon etc.)"),
    (340000, 349999, "FSFilter Undelete", "undelete"),
    (320000, 329999, "FSFilter Anti-Virus", "ANTI-VIRUS scanner (sits in every file open)"),
    (300000, 309999, "FSFilter Replication", "replication"),
    (280000, 289999, "FSFilter Continuous Backup", "continuous backup / CDP"),
    (260000, 269999, "FSFilter Content Screener", "content screener"),
    (240000, 249999, "FSFilter Quota Management", "quota / storage QoS"),
    (220000, 229999, "FSFilter System Recovery", "system recovery"),
    (200000, 209999, "FSFilter Cluster File System", "cluster filesystem"),
    (180000, 189999, "FSFilter HSM", "hierarchical storage / cloud files"),
    (170000, 174999, "FSFilter Imaging (co-installer)", "imaging"),
    (160000, 169999, "FSFilter Compression", "compression"),
    (140000, 149999, "FSFilter Encryption", "encryption (BitLocker/EFS/3rd-party)"),
    (130000, 139999, "FSFilter Virtualization", "virtualization (bindflt/wcifs)"),
    (120000, 129999, "FSFilter Physical Quota Management", "physical quota"),
    (100000, 109999, "FSFilter Open File", "open-file backup"),
    (80000, 89999, "FSFilter Security Enhancer", "security enhancer"),
    (60000, 69999, "FSFilter Copy Protection", "copy protection / DRM"),
    (40000, 49999, "FSFilter Bottom", "bottom instance"),
    (20000, 29999, "FSFilter System", "system"),
]


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def altitude_class(altitude):
    try:
        a = int(float(altitude))
    except (TypeError, ValueError):
        return (None, None)
    for lo, hi, cls, meaning in _ALT_RANGES:
        if lo <= a <= hi:
            return (cls, meaning)
    return (None, None)


def _run(args, timeout=30):
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    return r.stdout or ""


# --- minifilters -----------------------------------------------------------
def _parse_fltmc_filters(text):
    rows = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("Filter Name") or set(s) <= set("- "):
            continue
        toks = s.split()
        if len(toks) < 4:
            continue
        # columns: Name ... | NumInstances | Altitude | Frame  (last 3 numeric-ish)
        frame, altitude, instances = toks[-1], toks[-2], toks[-3]
        try:
            float(altitude)
            int(instances)
            int(frame)
        except ValueError:
            continue
        name = " ".join(toks[:-3])
        cls, meaning = altitude_class(altitude)
        rows.append({"name": name, "altitude": altitude, "altitude_class": cls,
                     "altitude_meaning": meaning, "instances": int(instances), "frame": int(frame)})
    return rows


def _service_imagepath(name):
    """Resolve a filter/service name to its driver binary path via the service registry ImagePath."""
    import winreg
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    ip = None
    try:
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r"SYSTEM\CurrentControlSet\Services\%s" % name, 0,
                           winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
        ip, _ = winreg.QueryValueEx(k, "ImagePath")
    except OSError:
        ip = None
    if ip:
        p = ip.strip().strip('"')
        low = p.lower()
        if low.startswith("\\systemroot\\"):
            p = os.path.join(windir, p[len("\\SystemRoot\\"):])
        elif low.startswith("\\??\\"):
            p = p[4:]
        elif low.startswith("system32\\") or low.startswith("\\system32\\"):
            p = os.path.join(windir, p.lstrip("\\"))
        elif not os.path.isabs(p):
            p = os.path.join(windir, "System32", "drivers", os.path.basename(p))
        if os.path.isfile(p):
            return p
    # fallback: drivers\<name>.sys
    fb = os.path.join(DRIVERS_DIR, name + ".sys")
    return fb if os.path.isfile(fb) else None


def _batch_company(paths):
    """One PowerShell call -> {path_lower: CompanyName}. Best-effort."""
    paths = [p for p in paths if p]
    if not paths:
        return {}
    arr = ",".join("'" + p.replace("'", "''") + "'" for p in paths)
    try:
        out = _run(_PS + [f"Get-Item -LiteralPath {arr} -ErrorAction SilentlyContinue | "
                          "Select-Object FullName,@{n='Company';e={$_.VersionInfo.CompanyName}} | "
                          "ConvertTo-Json -Compress"], timeout=40).strip()
        if not out:
            return {}
        data = json.loads(out)
        if isinstance(data, dict):
            data = [data]
        return {d.get("FullName", "").lower(): (d.get("Company") or "").strip() for d in data}
    except Exception:
        return {}


def minifilters(filter=None, third_party_only=False, enrich=True):
    if not is_admin():
        return {"error": "fltmc requires admin; start the server elevated.", "is_admin": False}
    try:
        rows = _parse_fltmc_filters(_run(["fltmc.exe", "filters"]))
    except Exception as e:
        return {"error": str(e)}
    flt = (filter or "").lower()
    rows = [r for r in rows if not flt or flt in r["name"].lower()]
    if enrich:
        for r in rows:
            r["driver_path"] = _service_imagepath(r["name"])
        companies = _batch_company([r["driver_path"] for r in rows])
        for r in rows:
            company = companies.get((r["driver_path"] or "").lower())
            r["company"] = company or None
            # third_party is True/False when the company is known, None when unresolved
            r["third_party"] = (("microsoft" not in company.lower()) if company else None)
    out = [r for r in rows if not (third_party_only and r.get("third_party") is not True)]
    out.sort(key=lambda r: float(r["altitude"]), reverse=True)
    tp = sum(1 for r in out if r.get("third_party") is True)
    return {"count": len(out), "third_party_count": tp, "minifilters": out}


def filter_instances(volume="C:"):
    if not is_admin():
        return {"error": "fltmc requires admin; start the server elevated.", "is_admin": False}
    if not (isinstance(volume, str) and len(volume) == 2 and volume[0].isalpha() and volume[1] == ":"):
        return {"error": "invalid volume (expected e.g. 'C:')"}
    try:
        text = _run(["fltmc.exe", "instances", "-v", volume])
    except Exception as e:
        return {"error": str(e)}
    rows = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("Filter") or s.startswith("Instances") or set(s) <= set("- "):
            continue
        toks = s.split()
        if len(toks) < 3:
            continue
        try:
            float(toks[1])
        except ValueError:
            continue
        name = toks[0]
        altitude = toks[1]
        cls, _ = altitude_class(altitude)
        # columns after Instance Name: Frame, SprtFtrs, [VlStatus blank] -> frame is toks[-2]
        frame = None
        if len(toks) > 4:
            instance_name = " ".join(toks[2:-2])
            try:
                frame = int(toks[-2])
            except ValueError:
                frame = None
        else:
            instance_name = " ".join(toks[2:])
        rows.append({"filter": name, "altitude": altitude, "altitude_class": cls,
                     "instance_name": instance_name, "frame": frame})
    return {"volume": volume, "count": len(rows), "instances": rows}


# --- network filters -------------------------------------------------------
def network_filters():
    res = {}
    try:
        out = _run(_PS + ["Get-NetAdapterBinding | Where-Object Enabled | Select-Object "
                          "Name,DisplayName,ComponentID | ConvertTo-Json -Compress"], timeout=25)
        data = json.loads(out) if out.strip() else []
        if isinstance(data, dict):
            data = [data]
        res["ndis_bindings"] = [{"adapter": d.get("Name"), "display": d.get("DisplayName"),
                                 "component_id": d.get("ComponentID"),
                                 "third_party": not str(d.get("ComponentID", "")).lower().startswith("ms_")}
                                for d in data]
    except Exception as e:
        res["ndis_error"] = str(e)
    try:
        lsps = _winsock_lsps()
        if lsps is None:
            res["winsock_error"] = "could not read the Winsock catalog registry"
        else:
            res["winsock_lsp"] = lsps
            res["winsock_lsp_count"] = len(lsps)
            res["winsock_third_party_count"] = sum(1 for e in lsps if e.get("third_party") is True)
    except Exception as e:
        res["winsock_error"] = str(e)
    return res


def _winsock_lsps():
    """Enumerate the Winsock LSP/base-provider catalog from the registry (locale-independent, unlike the
    localized `netsh winsock show catalog` text). Returns a list of {protocol, third_party} or None."""
    import re
    import winreg
    base = r"SYSTEM\CurrentControlSet\Services\WinSock2\Parameters\Protocol_Catalog9\Catalog_Entries"
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base, 0,
                              winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
    except OSError:
        return None
    out = []
    i = 0
    while True:
        try:
            sub = winreg.EnumKey(root, i)
        except OSError:
            break
        i += 1
        try:
            e = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base + "\\" + sub, 0,
                               winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
            item, _ = winreg.QueryValueEx(e, "PackedCatalogItem")
        except OSError:
            continue
        # WSAPROTOCOL_INFOW.szProtocol (the human-readable provider name, e.g. "MSAFD Tcpip [TCP/IP]")
        # is the longest printable-ASCII run in the packed struct -- decode UTF-16 and pick it.
        try:
            text = bytes(item).decode("utf-16-le", "ignore")
        except Exception:
            text = ""
        runs = re.findall(r"[ -~]{4,}", text)
        name = max(runs, key=len).strip() if runs else None
        # third-party only if a referenced DLL lives OUTSIDE the Windows system dirs (a real layered
        # LSP ships its DLL in a vendor dir). All Microsoft base/optional providers reference
        # System32/SysWOW64/%SystemRoot% DLLs -> not third-party.
        dll_refs = [r.lower() for r in runs if ".dll" in r.lower()]
        def _win_dir(r):
            return ("\\system32\\" in r or "\\syswow64\\" in r or "%systemroot%" in r or "mswsock" in r)
        third_party = any(not _win_dir(r) for r in dll_refs)
        out.append({"protocol": name, "third_party": third_party})
    return out


# --- baselines -------------------------------------------------------------
def _load():
    try:
        with open(BASELINE_PATH, "r", encoding="utf-8") as fh:
            obj = json.load(fh)
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data):
    d = os.path.dirname(BASELINE_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = BASELINE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, BASELINE_PATH)


def baseline_save(name="default"):
    mf = minifilters(enrich=False)
    if "error" in mf:
        return mf
    snap = {r["name"]: r["altitude"] for r in mf["minifilters"]}
    with _lock:
        data = _load()
        data[name] = {"ts": _now_iso(), "filters": snap}
        try:
            _save(data)
        except OSError as e:
            return {"error": f"could not write baseline: {e}"}
    return {"name": name, "ts": data[name]["ts"], "filter_count": len(snap)}


def baseline_diff(name="default"):
    with _lock:
        data = _load()
    entry = data.get(name)
    if not isinstance(entry, dict) or not isinstance(entry.get("filters"), dict):
        return {"error": f"no valid baseline named '{name}'; call baseline_save first",
                "available": list(data.keys())}
    mf = minifilters(enrich=False)
    if "error" in mf:
        return mf
    base = entry["filters"]
    now = {r["name"]: r["altitude"] for r in mf["minifilters"]}
    added = sorted(set(now) - set(base))
    removed = sorted(set(base) - set(now))

    def enrich(names):
        return [{"name": n, "altitude": now.get(n) or base.get(n),
                 "altitude_class": altitude_class(now.get(n) or base.get(n))[0]} for n in names]
    return {"name": name, "baseline_ts": entry.get("ts"),
            "added": enrich(added), "removed": enrich(removed),
            "summary": {"added": len(added), "removed": len(removed)}}


def health():
    h = {"is_admin": is_admin()}
    try:
        mf = minifilters(enrich=True)  # enrich so third_party_count is accurate
        if "error" in mf:
            h["fltmc_ok"] = False
            h["error"] = mf["error"]
        else:
            h["fltmc_ok"] = True
            h["minifilter_count"] = mf["count"]
            h["third_party_count"] = mf["third_party_count"]
    except Exception as e:
        h["fltmc_ok"] = False
        h["error"] = str(e)
    with _lock:
        h["baselines"] = list(_load().keys())
    return h
