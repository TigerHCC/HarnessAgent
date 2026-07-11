"""Windows Update history + failures + pending state. No MCP deps. Read-only.

Full WU history (incl. failures with HRESULTs) is only available via the WUA COM API
(Microsoft.Update.Session -> IUpdateSearcher.QueryHistory) -- Get-HotFix/QFE misses most of it. We call
the COM API through a PowerShell subprocess (clean, avoids COM-threading issues in the MCP server) and
decode WU/CBS HRESULTs the shell has no table for.
"""
import ctypes
import json
import re
import subprocess
import winreg

_PS = ["powershell", "-NoProfile", "-NonInteractive", "-Command"]

_OPERATION = {1: "Installation", 2: "Uninstallation", 3: "Other"}
_RESULT = {0: "NotStarted", 1: "InProgress", 2: "Succeeded", 3: "SucceededWithErrors",
           4: "Failed", 5: "Aborted"}

# Curated WU / CBS / servicing HRESULTs (the ones the shell can't explain). Keyed by lowercase 8-hex.
_HRESULTS = {
    "00000000": ("S_OK", "Success."),
    "80070002": ("ERROR_FILE_NOT_FOUND", "A required file was missing (often a corrupt download cache)."),
    "80070003": ("ERROR_PATH_NOT_FOUND", "A required path was missing."),
    "80070005": ("E_ACCESSDENIED", "Access denied (permissions / a service couldn't write)."),
    "8007000d": ("ERROR_INVALID_DATA", "Corrupt update data."),
    "80070490": ("ERROR_NOT_FOUND", "An element wasn't found (often component-store corruption)."),
    "800705b4": ("ERROR_TIMEOUT", "The operation timed out."),
    "80070643": ("ERROR_INSTALL_FAILURE", "Fatal install error (common for .NET / CU failures)."),
    "80070bc9": ("ERROR_FAIL_REBOOT_REQUIRED", "A reboot is required to finish; the update is half-applied."),
    "80073712": ("ERROR_SXS_COMPONENT_STORE_CORRUPT", "Component store (WinSxS) corrupt -> run DISM /RestoreHealth + SFC."),
    "800f0831": ("CBS_E_STORE_CORRUPTION", "CBS store corruption -> DISM /RestoreHealth."),
    "800f081f": ("CBS_E_SOURCE_MISSING", "Servicing payload/source missing -> DISM with a known-good source."),
    "800f0900": ("CBS_E_XML_PARSER_FAILURE", "Servicing manifest parse failure (corruption)."),
    "800f0922": ("CBS_E_INSTALLERS_FAILED", "A CU failed to install (often .NET, a pending op, or low disk)."),
    "800f0982": ("PSFX_E_MATCHING_COMPONENT_NOT_FOUND", "A required base component is missing (store mismatch)."),
    "80240022": ("WU_E_ALL_UPDATES_FAILED", "Every update in the batch failed."),
    "80240034": ("WU_E_DOWNLOAD_FAILED", "The update failed to download."),
    "80240438": ("WU_E_PT_ENDPOINT_UNREACHABLE", "Couldn't reach the WU endpoint (network/proxy/WSUS)."),
    "8024200b": ("WU_E_UH_INSTALLERFAILURE", "The update handler failed to install the update (often a driver package the vendor later superseded)."),
    "8024200d": ("WU_E_UH_NEEDANOTHERDOWNLOAD", "The handler needs another download (retry)."),
    "80242006": ("WU_E_UH_INVALIDMETADATA", "Invalid update metadata."),
    "8024402c": ("WU_E_PT_WINHTTP_NAME_NOT_RESOLVED", "DNS/proxy: the WU server name didn't resolve."),
    "80244019": ("WU_E_PT_HTTP_STATUS_NOT_FOUND", "HTTP 404 from the update server (bad WSUS content)."),
    "80248007": ("WU_E_DS_NODATA", "The WU datastore had no data for the request."),
    "8024a112": ("WU_E_UPDATE_UNDER_RESTART", "The update is deferred by an in-progress restart."),
    "80246007": ("WU_E_DM_NOTDOWNLOADED", "The update wasn't downloaded."),
    "80070020": ("ERROR_SHARING_VIOLATION", "A file was locked by another process during install."),
    "80070570": ("ERROR_FILE_CORRUPT", "A file was corrupt (disk or download)."),
    "80070008": ("ERROR_NOT_ENOUGH_MEMORY", "Out of memory during servicing."),
    "80070070": ("ERROR_DISK_FULL", "Not enough disk space to install the update."),
}


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def decode_hresult(code):
    if code is None:
        return (None, None)
    s = str(code).strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    try:
        val = int(s, 16) & 0xFFFFFFFF   # HRESULTs are always hex
    except ValueError:
        return (None, None)
    return _HRESULTS.get(f"{val:08x}", (None, None))


def _extract_kb(title):
    m = re.search(r"KB\d{6,7}", title or "")
    return m.group(0) if m else None


# emit UTF-8 so non-ASCII update titles (®, en-dash, localized names) survive (PS 5.1 stdout is OEM cp)
_ENC = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "


def _ps_json(cmd, timeout=60):
    r = subprocess.run(_PS + [_ENC + cmd], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    out = (r.stdout or "").strip()
    # a real failure (COM threw / WUA disabled / nonzero exit) must surface as an error, not empty data:
    # the queries always emit at least '[]' on success, so empty stdout + an error signal == failure
    if r.returncode != 0 or (not out and (r.stderr or "").strip()):
        err = ((r.stderr or "").strip() or out or "no output")
        raise RuntimeError(f"PowerShell failed (exit {r.returncode}): {err[:300]}")
    if not out:
        return None
    return json.loads(out)


def _as_list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


# --- public API ------------------------------------------------------------
def update_history(max=100, failures_only=False):
    try:
        n = int(max)
    except (TypeError, ValueError):
        n = 100
    if n < 1:
        n = 100
    cmd = (
        "$s = New-Object -ComObject Microsoft.Update.Session; "
        "$se = $s.CreateUpdateSearcher(); $c = $se.GetTotalHistoryCount(); "
        f"if ($c -le 0) {{ '[]' }} else {{ $h = $se.QueryHistory(0, [Math]::Min($c, {n})); "
        "$h | ForEach-Object { [PSCustomObject]@{ Date=$_.Date.ToUniversalTime().ToString('o'); "
        "Title=$_.Title; Operation=[int]$_.Operation; ResultCode=[int]$_.ResultCode; "
        "HResult=('0x{0:X8}' -f ($_.HResult -band 0xFFFFFFFF)) } } | ConvertTo-Json -Depth 3 -Compress }")
    try:
        data = _as_list(_ps_json(cmd))
    except Exception as e:
        return {"error": f"WUA QueryHistory failed: {e}"}
    rows = []
    for d in data:
        result = _RESULT.get(d.get("ResultCode"), d.get("ResultCode"))
        failed = d.get("ResultCode") in (3, 4, 5)
        if failures_only and not failed:
            continue
        name, meaning = decode_hresult(d.get("HResult"))
        rows.append({"date": d.get("Date"), "title": d.get("Title"), "kb": _extract_kb(d.get("Title")),
                     "operation": _OPERATION.get(d.get("Operation"), d.get("Operation")),
                     "result": result, "failed": failed, "hresult": d.get("HResult"),
                     "hresult_name": name, "hresult_meaning": meaning})
    return {"count": len(rows), "failures_only": bool(failures_only), "history": rows}


def installed_updates(max=200):
    # InstalledOn is a date-only value (local midnight); format without UTC conversion to avoid off-by-one
    cmd = ("Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object HotFixID,Description,"
           "@{n='InstalledOn';e={if($_.InstalledOn){$_.InstalledOn.ToString('yyyy-MM-dd')}}},"
           "InstalledBy | ConvertTo-Json -Depth 3 -Compress")
    try:
        data = _as_list(_ps_json(cmd))
    except Exception as e:
        return {"error": f"Get-HotFix failed: {e}"}
    try:
        n = int(max)
    except (TypeError, ValueError):
        n = 200
    if n < 1:
        n = 200
    rows = [{"kb": d.get("HotFixID"), "type": d.get("Description"),
             "installed_on": d.get("InstalledOn"), "installed_by": d.get("InstalledBy")}
            for d in data][:n]
    return {"count": len(rows), "hotfixes": rows}


def _reg_exists(hive, path):
    try:
        winreg.OpenKey(hive, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY).Close()
        return True
    except OSError:
        return False


def pending_state():
    res = {"is_admin": is_admin()}
    res["reboot_pending_cbs"] = _reg_exists(
        winreg.HKEY_LOCAL_MACHINE,
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending")
    res["reboot_required_wu"] = _reg_exists(
        winreg.HKEY_LOCAL_MACHINE,
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired")
    # pending file rename operations (a servicing op waiting for reboot)
    try:
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager")
        v, _ = winreg.QueryValueEx(k, "PendingFileRenameOperations")
        res["pending_file_renames"] = bool(v)
    except OSError:
        res["pending_file_renames"] = False
    # OS build (true patch level)
    try:
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r"SOFTWARE\Microsoft\Windows NT\CurrentVersion")
        cur, _ = winreg.QueryValueEx(k, "CurrentBuild")
        try:
            ubr, _ = winreg.QueryValueEx(k, "UBR")
        except OSError:
            ubr = None
        res["os_build"] = f"{cur}.{ubr}" if ubr is not None else str(cur)
    except OSError:
        pass
    res["reboot_pending"] = bool(res["reboot_pending_cbs"] or res["reboot_required_wu"]
                                 or res["pending_file_renames"])
    return res


def health():
    h = {"is_admin": is_admin(), "hresult_table_size": len(_HRESULTS)}
    try:
        cmd = ("$s = New-Object -ComObject Microsoft.Update.Session; "
               "$s.CreateUpdateSearcher().GetTotalHistoryCount()")
        r = subprocess.run(_PS + [cmd], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
        out = (r.stdout or "").strip()
        h["wua_ok"] = out.isdigit()
        h["history_count"] = int(out) if out.isdigit() else None
    except Exception as e:
        h["wua_ok"] = False
        h["error"] = str(e)
    try:
        h["reboot_pending"] = pending_state().get("reboot_pending")
    except Exception:
        pass
    return h
