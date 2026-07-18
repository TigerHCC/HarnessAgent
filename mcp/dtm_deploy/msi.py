"""Pure-Python reimplementation of the MSI install/uninstall logic in
ccp/tools/DTMTransmissionAutoTest-/Install-DTP.ps1 (Get-MsiProperty, RelatedProducts registry search,
Invoke-MsiWithProgress). Property reads + RelatedProducts use the WindowsInstaller.Installer COM object
(win32com); the actual install/uninstall action still shells out to msiexec.exe (there is no
COM-only way to run a full MSI install with logging) via subprocess.
"""
import codecs
import os
import re
import subprocess
import time

import win32com.client

UNINSTALL_ROOTS = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
)
_GUID_RE = re.compile(r"\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}")


def clean_string(s):
    return re.sub(r"[^\x20-\x7E]", "", s or "").strip()


def get_msi_property(msi_path, prop_name):
    installer = win32com.client.Dispatch("WindowsInstaller.Installer")
    db = installer.OpenDatabase(msi_path, 0)
    view = db.OpenView("SELECT Value FROM Property WHERE Property = '%s'" % prop_name)
    view.Execute()
    rec = view.Fetch()
    if rec is None:
        return None
    raw = str(rec.StringData(1))
    m = _GUID_RE.search(raw)
    if m:
        return m.group(0)
    return clean_string(raw)


def get_msi_properties(msi_path):
    return {
        "ProductCode": get_msi_property(msi_path, "ProductCode"),
        "ProductName": get_msi_property(msi_path, "ProductName"),
        "ProductVersion": get_msi_property(msi_path, "ProductVersion"),
        "UpgradeCode": get_msi_property(msi_path, "UpgradeCode"),
    }


def _related_product_codes(upgrade_code):
    installer = win32com.client.Dispatch("WindowsInstaller.Installer")
    try:
        related = installer.RelatedProducts(upgrade_code)
    except Exception:
        return []
    codes = []
    try:
        count = int(related.Count)
    except Exception:
        return []
    for i in range(count):
        try:
            codes.append(related.Item(i))
        except Exception:
            break
    return codes


def _registry_uninstall_entries():
    """Yields (product_code, display_name, display_version) for every HKLM Uninstall entry, both
    native and WOW6432Node views."""
    import winreg
    for root in UNINSTALL_ROOTS:
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root)
        except OSError:
            continue
        with key:
            i = 0
            while True:
                try:
                    sub_name = winreg.EnumKey(key, i)
                except OSError:
                    break
                i += 1
                if not sub_name.startswith("{"):
                    continue
                try:
                    with winreg.OpenKey(key, sub_name) as sub:
                        display_name, _ = winreg.QueryValueEx(sub, "DisplayName")
                        try:
                            display_version, _ = winreg.QueryValueEx(sub, "DisplayVersion")
                        except OSError:
                            display_version = ""
                except OSError:
                    continue
                yield sub_name, display_name, display_version


def find_products_to_uninstall(upgrade_code, product_name):
    """Mirrors Install-DTP.ps1's two-strategy lookup: RelatedProducts by UpgradeCode, falling back to
    a registry DisplayName match. Returns a list of product-code GUIDs."""
    if upgrade_code:
        codes = _related_product_codes(upgrade_code)
        if codes:
            return codes
    clean_target = clean_string(product_name)
    matches = []
    for code, display_name, _ in _registry_uninstall_entries():
        if display_name and clean_string(display_name) == clean_target:
            matches.append(code)
    return matches


def is_product_installed(product_name):
    clean_target = clean_string(product_name)
    for _, display_name, display_version in _registry_uninstall_entries():
        if display_name and clean_string(display_name) == clean_target:
            return True, display_version
    return False, None


def stop_dtp(service_name, process_name_patterns):
    """Stops the DTP service and kills any lingering Dell.TechHub*/Dell.CoreServices.Client* processes.
    Returns a dict describing what was done."""
    import fnmatch
    import win32service
    import win32serviceutil

    result = {"service_stopped": False, "processes_killed": []}
    try:
        status = win32serviceutil.QueryServiceStatus(service_name)[1]
        if status != win32service.SERVICE_STOPPED:
            win32serviceutil.StopService(service_name)
            for _ in range(30):
                if win32serviceutil.QueryServiceStatus(service_name)[1] == win32service.SERVICE_STOPPED:
                    result["service_stopped"] = True
                    break
                time.sleep(1)
    except Exception:
        pass  # service absent or already stopped -- not fatal

    try:
        import psutil
        for proc in psutil.process_iter(["pid", "name"]):
            name = proc.info.get("name") or ""
            if any(fnmatch.fnmatch(name, p) for p in process_name_patterns):
                try:
                    proc.kill()
                    result["processes_killed"].append({"pid": proc.info["pid"], "name": name})
                except Exception:
                    pass
        if result["processes_killed"]:
            time.sleep(2)
    except ImportError:
        result["processes_killed_error"] = "psutil not installed"
    return result


def run_msiexec(args_list, log_file):
    """Runs msiexec.exe /i or /x with the given extra args, logging to log_file. Returns (exit_code,
    log_file)."""
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    cmd = ["msiexec.exe"] + args_list + ["/quiet", "/norestart", "/l*v", log_file]
    proc = subprocess.run(cmd, capture_output=True, timeout=1800)
    return proc.returncode, log_file


def tail_log(log_file, n=40):
    """Last n lines of an msiexec verbose log. msiexec /l*v writes UTF-16LE (usually with a BOM);
    BOM-less UTF-16LE and plain UTF-8 are handled too. Never raises -- an unreadable file yields a
    single placeholder entry, because this feeds a result dict, not control flow."""
    try:
        with open(log_file, "rb") as f:
            raw = f.read()
        if raw.startswith(codecs.BOM_UTF16_LE) or raw.startswith(codecs.BOM_UTF16_BE):
            text = raw.decode("utf-16", errors="replace")
        elif b"\x00" in raw[:200]:
            text = raw.decode("utf-16-le", errors="replace")   # BOM-less msiexec log
        else:
            text = raw.decode("utf-8", errors="replace")
        return text.splitlines()[-n:]
    except OSError as e:
        return ["<unreadable: %s>" % e]


def uninstall_product(product_code, log_dir):
    safe_code = re.sub(r"[{}]", "", product_code)
    log_file = os.path.join(log_dir, "uninstall_%s.log" % safe_code)
    exit_code, log_file = run_msiexec(["/x", product_code], log_file)
    return {"product_code": product_code, "exit_code": exit_code, "log_file": log_file,
            "log_tail": tail_log(log_file),
            "reboot_required": exit_code == 3010, "success": exit_code in (0, 3010)}


def install_msi(msi_path, log_dir):
    props = get_msi_properties(msi_path)
    safe_code = re.sub(r"[^0-9A-Fa-f\-]", "", props.get("ProductCode") or "unknown")
    log_file = os.path.join(log_dir, "install_%s.log" % safe_code)
    exit_code, log_file = run_msiexec(["/i", msi_path], log_file)
    return {"msi_path": msi_path, "properties": props, "exit_code": exit_code, "log_file": log_file,
            "log_tail": tail_log(log_file),
            "reboot_required": exit_code == 3010, "success": exit_code in (0, 3010)}


def pending_reboot():
    import winreg
    paths = (
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired"),
    )
    for hive, path in paths:
        try:
            with winreg.OpenKey(hive, path):
                return True
        except OSError:
            continue
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\Session Manager") as key:
            winreg.QueryValueEx(key, "PendingFileRenameOperations")
            return True
    except OSError:
        return False
