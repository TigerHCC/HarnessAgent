"""Config-drift collectors: autoruns / services / programs / tasks. No MCP deps.

Each collector yields normalized items {category, key, name, detail} where `key` is unique within a
category. Pure stdlib (winreg + xml.etree). Read-only against the system.
"""
import ctypes
import hashlib
import json
import os
import winreg
import xml.etree.ElementTree as ET

TASKS_DIR = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "Tasks")

_START_TYPES = {0: "Boot", 1: "System", 2: "Automatic", 3: "Manual", 4: "Disabled"}
_SERVICE_TYPES = {1: "KernelDriver", 2: "FileSystemDriver", 4: "Adapter", 8: "RecognizerDriver",
                  16: "OwnProcess", 32: "ShareProcess", 272: "OwnProcess(Interactive)",
                  288: "ShareProcess(Interactive)"}

CATEGORIES = ("autoruns", "services", "programs", "tasks")


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def value_hash(detail):
    return hashlib.sha1(json.dumps(detail, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _open(hive, subkey):
    return winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)


def _enum_values(hive, subkey):
    out = []
    try:
        k = _open(hive, subkey)
    except OSError:
        return out
    n = winreg.QueryInfoKey(k)[1]
    for i in range(n):
        try:
            name, val, _typ = winreg.EnumValue(k, i)
            out.append((name, val))
        except OSError:
            continue
    return out


def _read_value(hive, subkey, name):
    try:
        k = _open(hive, subkey)
        v, _ = winreg.QueryValueEx(k, name)
        return v
    except OSError:
        return None


# --- autoruns --------------------------------------------------------------
_RUN_LOCATIONS = [
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run", "HKLM\\Run"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce", "HKLM\\RunOnce"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Run", "HKLM\\Wow6432\\Run"),
    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run", "HKCU\\Run"),
    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce", "HKCU\\RunOnce"),
]
_STARTUP_DIRS = [
    ("ProgramData\\Startup", os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                                          "Microsoft", "Windows", "Start Menu", "Programs", "Startup")),
]
if os.environ.get("APPDATA"):
    _STARTUP_DIRS.append(("User\\Startup", os.path.join(os.environ["APPDATA"],
                          "Microsoft", "Windows", "Start Menu", "Programs", "Startup")))


def collect_autoruns():
    for hive, subkey, label in _RUN_LOCATIONS:
        for name, val in _enum_values(hive, subkey):
            if not name and not str(val).strip():
                continue  # skip an empty (Default) value
            yield {"category": "autoruns", "key": f"{label}|{name}", "name": name,
                   "detail": {"location": label, "command": str(val)}}
    # Winlogon Shell / Userinit
    for vname in ("Shell", "Userinit"):
        v = _read_value(winreg.HKEY_LOCAL_MACHINE,
                        r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon", vname)
        if v is not None:
            yield {"category": "autoruns", "key": f"Winlogon|{vname}", "name": vname,
                   "detail": {"location": "Winlogon", "command": str(v)}}
    # Startup folders
    for label, d in _STARTUP_DIRS:
        try:
            entries = list(os.scandir(d))
        except (OSError, PermissionError):
            continue
        for e in entries:
            if e.is_file():
                yield {"category": "autoruns", "key": f"{label}|{e.name}", "name": e.name,
                       "detail": {"location": label, "command": e.path}}


# --- services / drivers ----------------------------------------------------
def collect_services():
    try:
        base = _open(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services")
    except OSError:
        return
    i = 0
    while True:
        try:
            svc = winreg.EnumKey(base, i)
        except OSError:
            break
        i += 1
        image = _read_value(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\%s" % svc, "ImagePath")
        start = _read_value(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\%s" % svc, "Start")
        stype = _read_value(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\%s" % svc, "Type")
        disp = _read_value(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services\%s" % svc, "DisplayName")
        if image is None and start is None and stype is None:
            continue  # container key, not a real service/driver
        detail = {
            "image": str(image) if image is not None else None,
            "start": _START_TYPES.get(start, start) if isinstance(start, int) else start,
            "type": _SERVICE_TYPES.get(stype, stype) if isinstance(stype, int) else stype,
            "display": str(disp) if disp is not None else None,
        }
        yield {"category": "services", "key": svc, "name": disp or svc, "detail": detail}


# --- programs (Uninstall) --------------------------------------------------
_UNINSTALL = [
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "HKLM"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall", "HKLM\\Wow6432"),
    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "HKCU"),
]


def collect_programs():
    for hive, subkey, label in _UNINSTALL:
        try:
            base = _open(hive, subkey)
        except OSError:
            continue
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(base, i)
            except OSError:
                break
            i += 1
            disp = _read_value(hive, subkey + "\\" + sub, "DisplayName")
            if not disp:
                continue
            detail = {
                "name": str(disp),
                "version": _read_value(hive, subkey + "\\" + sub, "DisplayVersion"),
                "publisher": _read_value(hive, subkey + "\\" + sub, "Publisher"),
                "install_date": _read_value(hive, subkey + "\\" + sub, "InstallDate"),
            }
            detail = {k: (str(v) if v is not None else None) for k, v in detail.items()}
            yield {"category": "programs", "key": f"{label}\\{sub}", "name": str(disp), "detail": detail}


# --- scheduled tasks -------------------------------------------------------
_TASK_NS = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"


def _parse_task_xml(path):
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        # Task XML is usually UTF-16; ET handles the declaration, but decode defensively
        root = ET.fromstring(raw)
    except (ET.ParseError, OSError, ValueError):
        return None
    ns = _TASK_NS
    exec_el = root.find(f"{ns}Actions/{ns}Exec")
    command = args = None
    if exec_el is not None:
        c = exec_el.find(f"{ns}Command")
        a = exec_el.find(f"{ns}Arguments")
        command = c.text if c is not None else None
        args = a.text if a is not None else None
    en = root.find(f"{ns}Settings/{ns}Enabled")
    enabled = (en.text if en is not None else None)
    triggers = root.find(f"{ns}Triggers")
    ntrig = len(list(triggers)) if triggers is not None else 0
    return {"command": command, "args": args, "enabled": enabled, "triggers": ntrig}


def collect_tasks():
    if not os.path.isdir(TASKS_DIR):
        return
    for root, _dirs, files in os.walk(TASKS_DIR):
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, TASKS_DIR).replace("\\", "/")
            detail = _parse_task_xml(full)
            if detail is None:
                detail = {"command": None, "args": None, "enabled": None, "triggers": 0, "unparsed": True}
            yield {"category": "tasks", "key": rel, "name": rel, "detail": detail}


_COLLECTORS = {
    "autoruns": collect_autoruns,
    "services": collect_services,
    "programs": collect_programs,
    "tasks": collect_tasks,
}


def collect(category=None):
    """Return (items, errors). items: list of normalized dicts (with value_hash added)."""
    cats = [category] if category else list(CATEGORIES)
    items = []
    errors = {}
    for cat in cats:
        fn = _COLLECTORS.get(cat)
        if fn is None:
            errors[cat] = "unknown category"
            continue
        try:
            for it in fn():
                it["value_hash"] = value_hash(it["detail"])
                items.append(it)
        except Exception as e:  # a collector must never abort the whole snapshot
            errors[cat] = str(e)
    return items, errors
