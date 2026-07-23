"""Audio-glitch diagnostics: lightweight risk indicators by default (sample-rate mismatch, power
management, recent driver errors) and an optional short ETW trace of Microsoft-Windows-Audio glitch
events. detect_sample_rate_mismatch is pure; the trace and event-log reads are guarded."""
import json
import subprocess


def detect_sample_rate_mismatch(active_formats):
    rates = set(str(f) for f in (active_formats or []) if f)
    return len(rates) > 1


def recent_audio_errors(timeout=30):
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
            "Get-WinEvent -FilterHashtable @{LogName='System';Level=1,2} -MaxEvents 200 "
            "-ErrorAction SilentlyContinue | Where-Object { $_.ProviderName -match 'audio|HdAudio|Realtek|USBAUDIO' } | "
            "Select-Object -First 15 @{n='time';e={$_.TimeCreated.ToString('s')}},"
            "@{n='provider';e={$_.ProviderName}},@{n='id';e={$_.Id}} | ConvertTo-Json"],
            capture_output=True, text=True, timeout=timeout)
        out = (r.stdout or "").strip()
        if not out:
            return []
        data = json.loads(out)
        return data if isinstance(data, list) else [data]
    except Exception:
        return []


def glitch_indicators(active_formats=None, timeout=30):
    return {"sample_rate_mismatch": detect_sample_rate_mismatch(active_formats),
            "active_formats": list(active_formats or []),
            "recent_driver_errors": recent_audio_errors(timeout)}


def _run_trace(seconds, timeout):
    """Best-effort short ETW trace via wpr (Windows Performance Recorder). Elevated. Returns dict."""
    # start a light trace, sleep, stop to a temp etl, then (optionally) summarize. Kept minimal here;
    # a full parse of the .etl is out of scope -- we report that a trace ran and any errors surfaced.
    import tempfile, os, time
    etl = os.path.join(tempfile.gettempdir(), "winaudio_glitch.etl")
    subprocess.run(["wpr", "-start", "GeneralProfile", "-filemode"], capture_output=True, timeout=timeout, check=True)
    time.sleep(seconds)
    subprocess.run(["wpr", "-stop", etl], capture_output=True, timeout=timeout, check=True)
    return {"ran": True, "events": [], "etl": etl, "secs": seconds,
            "note": "trace captured to etl; open in WPA for DPC/glitch analysis"}


def short_trace(seconds, max_seconds=30, timeout=60):
    secs = max(1, min(int(seconds), int(max_seconds)))
    try:
        return _run_trace(secs, timeout)
    except Exception as e:
        return {"ran": False, "error": "trace unavailable (%s: %s)" % (type(e).__name__, e)}
