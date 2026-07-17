"""Pure-Python reimplementation of Verify-Collection.ps1 and Verify-Heartbeat.ps1.

Collection verification runs DtpInstrumentationUtil.exe collect and checks for expected output
messages. Heartbeat verification advances the system date, waits for a new heartbeat cycle in the
DTP transmission log, then verifies OTP/HB success responses -- restoring the system date afterwards
regardless of outcome (mirrors the PowerShell script's try/finally).
"""
import glob
import os
import re
import subprocess
import time

EXPECTED_COLLECTION_MESSAGES = (
    "Initializing the Instrumentation Client SDK...",
    "Instrumentation SDK availability changed: Initialized",
    "Initialized successfully",
    "On-demand collection has been done",
)

SDK_INIT_PATTERN = re.compile(r"\*{3,}\s*SDK\s+Initialized\s*\*{3,}")
HB_OK_PATTERN = re.compile(r"Act:Response,\s*Type:HB,\s*Status:Success,\s*RC:1:1:20")
OTP_OK_PATTERN = re.compile(r"Act:Response,\s*Type:OTP,\s*Status:Success,\s*RC:2:1:20")
RETRY_PATTERN = re.compile(r"TXAction:Retry Later\(60min\)")
STARTED_HB_PATTERN = re.compile(r"Started HB scheduler")
STARTED_OTP_PATTERN = re.compile(r"Started OTP scheduler")


class VerifyError(Exception):
    pass


def find_instrumentation_exe(download_path):
    if not download_path or not os.path.isdir(download_path):
        raise VerifyError("download_path does not exist: %r" % download_path)
    candidates = sorted(
        glob.glob(os.path.join(download_path, "**", "DTPSamples*"), recursive=True),
        key=lambda p: os.path.getmtime(p), reverse=True,
    )
    for candidate in candidates:
        exe = os.path.join(candidate, "Samples", "DtpInstrumentationUtil.SubAgent", "bin", "Release",
                           "DtpInstrumentationUtil.exe")
        if os.path.isfile(exe):
            return exe
    raise VerifyError("no DtpInstrumentationUtil.exe found under any DTPSamples* folder in %r" % download_path)


def verify_collection(download_path, datatype_name="CameraInfo", timeout=120):
    exe = find_instrumentation_exe(download_path)
    proc = subprocess.run([exe, "collect", "--datatype-name", datatype_name],
                          capture_output=True, text=True, timeout=timeout)
    output = "\n".join([proc.stdout or "", proc.stderr or ""])

    found = {msg: (msg in output) for msg in EXPECTED_COLLECTION_MESSAGES}
    all_found = all(found.values())
    return {
        "datatype_name": datatype_name, "exit_code": proc.returncode,
        "expected_messages": found, "all_expected_found": all_found,
        "success": proc.returncode == 0 and all_found,
        "output_tail": output[-4000:],
    }


def resolve_heartbeat_log_path(configured_path=""):
    if configured_path and os.path.isfile(configured_path):
        return configured_path
    log_dir = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"), "Dell", "DTP", "Logs",
                           "DTM.Transmission")
    if not os.path.isdir(log_dir):
        raise VerifyError("transmission log directory not found: %s" % log_dir)
    candidates = [f for f in glob.glob(os.path.join(log_dir, "DTM.Transmission*")) if os.path.isfile(f)]
    if not candidates:
        raise VerifyError("no DTM.Transmission* files found in %s" % log_dir)
    return max(candidates, key=os.path.getmtime)


def _read_lines(log_path):
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        return f.readlines()


def _last_scheduler_index(lines):
    """Returns (index_of_last_scheduler_start_line, has_hb, has_otp), scanning from the end."""
    has_hb = has_otp = False
    idx = -1
    for j in range(len(lines) - 1, -1, -1):
        line = lines[j]
        if not has_hb and STARTED_HB_PATTERN.search(line):
            has_hb = True
            idx = max(idx, j)
        if not has_otp and STARTED_OTP_PATTERN.search(line):
            has_otp = True
            idx = max(idx, j)
        if has_hb or has_otp:
            break
    return idx, has_hb, has_otp


def _poll_once(log_path):
    """One poll iteration; returns (done: bool, note: str)."""
    lines = _read_lines(log_path)
    idx, has_hb, has_otp = _last_scheduler_index(lines)
    if idx < 0 or not has_hb:
        return False, "no scheduler found yet"
    after = lines[idx + 1:]
    after_text = "".join(after)
    hb_ok = bool(HB_OK_PATTERN.search(after_text))
    otp_ok = bool(OTP_OK_PATTERN.search(after_text))
    any_retry = bool(RETRY_PATTERN.search(after_text))

    if has_hb and not has_otp:
        if hb_ok:
            return True, "HB Response Success found"
        if any_retry:
            return True, "HB scheduler only, server responded Retry Later(60min)"
        return False, "waiting for HB response"

    # has_hb and has_otp
    hb_retry = bool(re.search(r"TXAction:Retry Later\(60min\).*Type:HB", after_text)) or \
        bool(re.search(r"Type:HB.*TXAction:Retry Later\(60min\)", after_text))
    otp_retry = bool(re.search(r"TXAction:Retry Later\(60min\).*Type:OTP", after_text)) or \
        bool(re.search(r"Type:OTP.*TXAction:Retry Later\(60min\)", after_text))
    if hb_ok and otp_ok:
        return True, "Both HB and OTP Response Success found"
    if hb_retry and otp_retry:
        return True, "Both HB and OTP in Retry Later(60min) state"
    if (hb_ok and otp_retry) or (otp_ok and hb_retry):
        return True, "One response succeeded, other in Retry Later(60min)"
    return False, "waiting for HB/OTP responses"


def _advance_system_date(days):
    import win32api
    now_utc = win32api.GetSystemTime()  # (year, month, dayOfWeek, day, hour, min, sec, ms)
    try:
        subprocess.run(["net", "stop", "w32time"], capture_output=True, timeout=30)
    except Exception:
        pass
    new_time = list(now_utc)
    new_time[3] += days  # day field
    win32api.SetSystemTime(new_time[0], new_time[1], 0, new_time[3], new_time[4], new_time[5],
                           new_time[6], new_time[7])
    return now_utc


def _restore_system_date(original_utc):
    import win32api
    win32api.SetSystemTime(original_utc[0], original_utc[1], 0, original_utc[3], original_utc[4],
                           original_utc[5], original_utc[6], original_utc[7])
    try:
        subprocess.run(["net", "start", "w32time"], capture_output=True, timeout=30)
    except Exception:
        pass


def verify_heartbeat(log_path="", advance_days=1, wait_seconds=3300, poll_interval=180,
                     skip_date_change=False, build_version=""):
    """Returns a result dict with status in {"success", "retrying", "failed"}. Restores the system
    date in a finally block regardless of outcome, mirroring Verify-Heartbeat.ps1."""
    resolved_log = resolve_heartbeat_log_path(log_path)
    original_utc = None
    try:
        if not skip_date_change:
            original_utc = _advance_system_date(advance_days)
            deadline = time.time() + wait_seconds
            found = False
            note = "timeout"
            while time.time() < deadline:
                time.sleep(min(poll_interval, max(0, deadline - time.time())))
                try:
                    found, note = _poll_once(resolved_log)
                except FileNotFoundError:
                    note = "log file not found during poll"
                    continue
                if found:
                    break

        lines = _read_lines(resolved_log)
        last_sdk_idx = -1
        for i in range(len(lines) - 1, -1, -1):
            if SDK_INIT_PATTERN.search(lines[i]):
                last_sdk_idx = i
                break
        if last_sdk_idx < 0:
            return {"status": "failed", "reason": "no SDK Initialized section found in log",
                    "log_path": resolved_log}

        section_text = "".join(lines[last_sdk_idx:])
        checks = {
            "sdk_initialized": bool(SDK_INIT_PATTERN.search(section_text)),
        }
        has_otp = bool(STARTED_OTP_PATTERN.search(section_text))
        has_hb = bool(STARTED_HB_PATTERN.search(section_text))
        if not has_otp and not has_hb:
            return {"status": "failed", "reason": "neither OTP nor HB scheduler started",
                    "log_path": resolved_log}

        all_passed = checks["sdk_initialized"]
        has_retry = bool(RETRY_PATTERN.search(section_text))
        if has_otp:
            checks["otp_scheduler_started"] = True
            otp_ok = bool(OTP_OK_PATTERN.search(section_text))
            checks["otp_response_success"] = otp_ok
            all_passed = all_passed and (otp_ok or has_retry)
        if has_hb:
            checks["hb_scheduler_started"] = True
            hb_ok = bool(HB_OK_PATTERN.search(section_text))
            checks["hb_response_success"] = hb_ok
            all_passed = all_passed and (hb_ok or has_retry)

        if build_version:
            checks["build_version_found"] = ("Collector Version:%s" % build_version) in section_text.replace(" ", "") \
                or build_version in section_text
            all_passed = all_passed and checks["build_version_found"]

        if all_passed and not has_retry:
            status = "success"
        elif all_passed and has_retry:
            status = "retrying"
        else:
            status = "failed"
        return {"status": status, "checks": checks, "log_path": resolved_log}
    finally:
        if original_utc is not None:
            _restore_system_date(original_utc)
