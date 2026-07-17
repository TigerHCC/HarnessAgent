"""Pure-Python reimplementation of Enable-Transmission.ps1 -- runs DTMUtil.exe configure-orchestrator
to enable realtime + midnight transmission, and (for DTP > 3.9) sets telemetry-notice-displayed.
"""
import glob
import os
import subprocess

import msi as msi_mod


class TransmissionError(Exception):
    pass


def find_dtmutil_exe(download_path):
    """Searches download_path for the newest DTPSamples* folder's DTMUtil.exe, mirroring
    Enable-Transmission.ps1's resolution logic."""
    if not download_path or not os.path.isdir(download_path):
        raise TransmissionError("download_path does not exist: %r" % download_path)
    candidates = sorted(
        glob.glob(os.path.join(download_path, "**", "DTPSamples*"), recursive=True),
        key=lambda p: os.path.getmtime(p), reverse=True,
    )
    for candidate in candidates:
        exe = os.path.join(candidate, "Samples", "DTMUtil", "bin", "Release", "DTMUtil.exe")
        if os.path.isfile(exe):
            return exe
    raise TransmissionError("no DTMUtil.exe found under any DTPSamples* folder in %r" % download_path)


def _run_dtmutil(exe_path, args, timeout=120):
    proc = subprocess.run([exe_path] + args, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def _installed_dtp_version():
    for _, display_name, display_version in msi_mod._registry_uninstall_entries():
        if display_name and "DTP" in display_name and display_version:
            try:
                return tuple(int(x) for x in display_version.split("."))
            except ValueError:
                continue
    return None


def enable_transmission(dtmutil_path):
    exit_code, stdout, stderr = _run_dtmutil(
        dtmutil_path, ["configure-orchestrator", "--realtime-transmission", "enabled",
                       "--midnight-transmission", "enabled"])
    if exit_code != 0:
        raise TransmissionError("configure-orchestrator exited with code %s: %s" % (exit_code, stderr or stdout))

    result = {"realtime_transmission": "enabled", "midnight_transmission": "enabled",
              "telemetry_notice": "N/A (DTP <= 3.9 or version unknown)"}

    version = _installed_dtp_version()
    if version and version > (3, 9, 0, 0):
        exit_code2, stdout2, stderr2 = _run_dtmutil(
            dtmutil_path, ["configure-orchestrator", "--telemetry-notice-displayed", "1.0.0"])
        if exit_code2 == 0:
            result["telemetry_notice"] = "set (1.0.0)"
        else:
            result["telemetry_notice"] = "failed (exit %s): %s" % (exit_code2, stderr2 or stdout2)
    return result
