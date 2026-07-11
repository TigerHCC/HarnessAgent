import winupdate as w


def test_decode_hresult():
    name, meaning = w.decode_hresult("0x800f0922")
    assert name == "CBS_E_INSTALLERS_FAILED" and meaning
    assert w.decode_hresult("0x8024200B")[0] == "WU_E_UH_INSTALLERFAILURE"  # case-insensitive
    assert w.decode_hresult("0x00000000")[0] == "S_OK"
    assert w.decode_hresult("0xDEADBEEF") == (None, None)   # uncurated
    assert w.decode_hresult(None) == (None, None)
    assert w.decode_hresult("garbage") == (None, None)


def test_extract_kb():
    assert w._extract_kb("2026-06 Cumulative Update (KB5094126)") == "KB5094126"
    assert w._extract_kb("Intel Display Driver Update") is None
    assert w._extract_kb(None) is None


def test_as_list():
    assert w._as_list(None) == []
    assert w._as_list({"a": 1}) == [{"a": 1}]
    assert w._as_list([1, 2]) == [1, 2]


# --- real-data smoke -------------------------------------------------------
def test_update_history_real():
    r = w.update_history(max=5)
    assert "error" not in r, r
    assert "history" in r
    for h in r["history"]:
        assert "date" in h and "result" in h and "failed" in h
        assert h["failed"] in (True, False)


def test_update_history_failures_only_real():
    r = w.update_history(max=200, failures_only=True)
    assert "error" not in r
    for h in r["history"]:
        assert h["failed"] is True


def test_update_history_bad_max_defaults():
    # non-numeric max must not raise (shadowed-builtin regression guard)
    r = w.update_history(max="lots")
    assert "error" not in r and "history" in r


def test_installed_updates_real():
    r = w.installed_updates(max=5)
    assert "error" not in r
    assert "hotfixes" in r
    for x in r["hotfixes"]:
        assert "kb" in x


def test_pending_state_real():
    p = w.pending_state()
    for k in ("reboot_pending", "reboot_pending_cbs", "pending_file_renames"):
        assert k in p and isinstance(p[k], bool)


def test_health_real():
    h = w.health()
    assert "is_admin" in h and "wua_ok" in h and "hresult_table_size" in h
