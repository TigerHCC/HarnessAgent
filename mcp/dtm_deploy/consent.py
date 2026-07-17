"""Pure-Python reimplementation of Enable-UserConsent.ps1 -- writes the DTP telemetry consent
DWORD registry value under HKLM and reads it back to verify.
"""
import winreg

_HIVE_MAP = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKEY_LOCAL_MACHINE": winreg.HKEY_LOCAL_MACHINE}


def _split_path(registry_path):
    """'HKLM\\SOFTWARE\\Dell\\...' -> (winreg hive constant, 'SOFTWARE\\Dell\\...')."""
    hive_name, _, sub_path = registry_path.partition("\\")
    hive = _HIVE_MAP.get(hive_name.upper())
    if hive is None:
        raise ValueError("unsupported registry hive %r (only HKLM is supported)" % hive_name)
    return hive, sub_path


def enable_user_consent(registry_path, value_name, value_data):
    """Writes value_name=value_data (REG_DWORD) under registry_path, creating the key if needed, and
    reads it back to verify. Returns a result dict; raises on failure (caller should catch)."""
    hive, sub_path = _split_path(registry_path)
    key = winreg.CreateKeyEx(hive, sub_path, 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE)
    try:
        winreg.SetValueEx(key, value_name, 0, winreg.REG_DWORD, int(value_data))
        written, _ = winreg.QueryValueEx(key, value_name)
    finally:
        winreg.CloseKey(key)
    if int(written) != int(value_data):
        raise RuntimeError("readback verification failed: expected %s, found %s" % (value_data, written))
    return {"registry_path": registry_path, "value_name": value_name, "value_data": int(written)}
