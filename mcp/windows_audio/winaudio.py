"""Non-pycaw audio sources: Windows services, PnP (incl. Bluetooth A2DP/HFP), and microphone
privacy (registry). RAW acquisition uses subprocess/winreg; classify_bt / summarize_mic_privacy are
pure and unit-tested. Each source is individually guarded so one failure never fails the tool."""
import json
import subprocess


def _ps_json(command, timeout):
    """Run a PowerShell command and parse its JSON stdout; [] on any failure."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", command],
                           capture_output=True, text=True, timeout=timeout)
        out = (r.stdout or "").strip()
        if not out:
            return []
        data = json.loads(out)
        return data if isinstance(data, list) else [data]
    except Exception:
        return []


def services(timeout=30):
    rows = _ps_json("Get-Service Audiosrv,AudioEndpointBuilder | "
                    "Select-Object Name,@{n='Status';e={[string]$_.Status}} | ConvertTo-Json", timeout)
    return {r.get("Name"): {"status": r.get("Status")} for r in rows if r.get("Name")}


def pnp_audio(timeout=30):
    return _ps_json(
        "Get-PnpDevice -Class MEDIA,AudioEndpoint,Bluetooth -ErrorAction SilentlyContinue | "
        "Select-Object @{n='status';e={[string]$_.Status}},@{n='name';e={$_.FriendlyName}},"
        "@{n='class';e={$_.Class}} | ConvertTo-Json", timeout)


def classify_bt(name):
    n = (name or "").lower()
    if "hands-free" in n or n.endswith(" hf") or "hfp" in n or "hands free" in n:
        return "hfp"
    if "a2dp" in n:
        return "a2dp"
    return "a2dp" if n else "other"


def mic_privacy():
    """Global + per-app microphone access from CapabilityAccessManager\\ConsentStore\\microphone."""
    import winreg
    def read(root):
        base = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
        try:
            k = winreg.OpenKey(root, base)
        except OSError:
            return None, {}
        gval = None
        try:
            gval, _ = winreg.QueryValueEx(k, "Value")
        except OSError:
            pass
        apps = {}
        try:
            for i in range(winreg.QueryInfoKey(k)[0]):
                sub = winreg.EnumKey(k, i)
                try:
                    sk = winreg.OpenKey(k, sub)
                    v, _ = winreg.QueryValueEx(sk, "Value")
                    apps[sub] = v
                except OSError:
                    pass
        except OSError:
            pass
        return gval, apps
    gval, apps = read(winreg.HKEY_CURRENT_USER)
    return summarize_mic_privacy(gval or "unset", apps)


def summarize_mic_privacy(global_val, app_vals):
    denied = sorted([a for a, v in (app_vals or {}).items() if str(v).lower() == "deny"])
    return {"global": global_val, "denied_apps": denied,
            "app_count": len(app_vals or {})}
