import types

import msi


def test_clean_string_strips_non_ascii_and_trims():
    assert msi.clean_string("  Dell\u200bTechHub  ") == "DellTechHub"


def test_clean_string_handles_none():
    assert msi.clean_string(None) == ""


def test_uninstall_product_reports_reboot_required(monkeypatch, tmp_path):
    monkeypatch.setattr(msi, "run_msiexec", lambda args, log_file: (3010, log_file))
    result = msi.uninstall_product("{ABCDEF12-0000-0000-0000-000000000000}", str(tmp_path))
    assert result["reboot_required"] is True
    assert result["success"] is True


def test_uninstall_product_reports_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(msi, "run_msiexec", lambda args, log_file: (1603, log_file))
    result = msi.uninstall_product("{ABCDEF12-0000-0000-0000-000000000000}", str(tmp_path))
    assert result["success"] is False
    assert result["exit_code"] == 1603


def test_install_msi_reports_success(monkeypatch, tmp_path):
    monkeypatch.setattr(msi, "get_msi_properties", lambda p: {"ProductCode": "{X}", "ProductName": "N",
                                                               "ProductVersion": "1.0", "UpgradeCode": "{U}"})
    monkeypatch.setattr(msi, "run_msiexec", lambda args, log_file: (0, log_file))
    result = msi.install_msi("fake.msi", str(tmp_path))
    assert result["success"] is True
    assert result["reboot_required"] is False


def test_find_products_to_uninstall_prefers_upgrade_code(monkeypatch):
    monkeypatch.setattr(msi, "_related_product_codes", lambda code: ["{A}", "{B}"])
    codes = msi.find_products_to_uninstall("{UPGRADE}", "Anything")
    assert codes == ["{A}", "{B}"]


def test_find_products_to_uninstall_falls_back_to_registry(monkeypatch):
    monkeypatch.setattr(msi, "_related_product_codes", lambda code: [])
    monkeypatch.setattr(msi, "_registry_uninstall_entries",
                        lambda: iter([("{C}", "My Product", "1.0"), ("{D}", "Other", "2.0")]))
    codes = msi.find_products_to_uninstall("", "My Product")
    assert codes == ["{C}"]


def test_tail_log_utf16_bom(tmp_path):
    p = tmp_path / "install.log"
    lines = ["line %d" % i for i in range(50)]
    p.write_text("\n".join(lines), encoding="utf-16")      # writes BOM
    tail = msi.tail_log(str(p), n=40)
    assert len(tail) == 40
    assert tail[0] == "line 10" and tail[-1] == "line 49"


def test_tail_log_utf16le_no_bom(tmp_path):
    p = tmp_path / "install.log"
    p.write_bytes("alpha\nbeta".encode("utf-16-le"))       # BOM-less UTF-16LE
    assert msi.tail_log(str(p)) == ["alpha", "beta"]


def test_tail_log_utf8_fallback(tmp_path):
    p = tmp_path / "install.log"
    p.write_text("one\ntwo\nthree", encoding="utf-8")
    assert msi.tail_log(str(p), n=2) == ["two", "three"]


def test_tail_log_missing_file_returns_placeholder(tmp_path):
    tail = msi.tail_log(str(tmp_path / "nope.log"))
    assert len(tail) == 1 and tail[0].startswith("<unreadable:")


def test_results_include_log_tail(monkeypatch, tmp_path):
    log = tmp_path / "u.log"
    log.write_text("msi ok", encoding="utf-16")
    monkeypatch.setattr(msi, "run_msiexec", lambda args, log_file: (0, str(log)))
    r = msi.uninstall_product("{ABCDEF12-0000-0000-0000-000000000000}", str(tmp_path))
    assert r["log_tail"] == ["msi ok"]
    monkeypatch.setattr(msi, "get_msi_properties",
                        lambda p: {"ProductCode": "{X}", "ProductName": "N",
                                   "ProductVersion": "1.0", "UpgradeCode": "{U}"})
    r2 = msi.install_msi("C:/fake.msi", str(tmp_path))
    assert "log_tail" in r2
