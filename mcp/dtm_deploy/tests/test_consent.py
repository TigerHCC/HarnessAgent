import pytest

import consent


def test_split_path_supports_hklm():
    hive, sub = consent._split_path(r"HKLM\SOFTWARE\Dell\Notification Manager\Telemetry")
    import winreg
    assert hive == winreg.HKEY_LOCAL_MACHINE
    assert sub == r"SOFTWARE\Dell\Notification Manager\Telemetry"


def test_split_path_rejects_unknown_hive():
    with pytest.raises(ValueError):
        consent._split_path(r"HKCU\SOFTWARE\x")


def test_enable_user_consent_roundtrip(monkeypatch, tmp_path):
    """Exercises the real winreg calls against a private test root under
    HKEY_CURRENT_USER\\Software (safe to create/delete, does not touch HKLM), by monkeypatching the
    hive map so 'HKLM' in this test resolves to HKCU."""
    import winreg
    monkeypatch.setitem(consent._HIVE_MAP, "HKLM", winreg.HKEY_CURRENT_USER)
    test_path = r"HKLM\Software\_dtm_deploy_test\Telemetry"
    try:
        result = consent.enable_user_consent(test_path, "ConsentOverride", 1)
        assert result["value_data"] == 1
    finally:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\_dtm_deploy_test\Telemetry")
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\_dtm_deploy_test")
        except OSError:
            pass
