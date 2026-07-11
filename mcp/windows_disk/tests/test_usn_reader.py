import usn_reader as u


def test_ft_conversion():
    assert u._ft_to_dt(0) is None
    assert u._ft_to_dt(None) is None
    assert u._ft_to_dt(9 * 10 ** 20) is None                 # overflow guard
    when = u._ft_to_dt(133000000000000000)
    assert when is not None and when.year >= 2022


def test_reason_names():
    names = u._reason_names(0x00000100 | 0x80000000)          # FILE_CREATE + CLOSE
    assert "FILE_CREATE" in names and "CLOSE" in names
    assert u._reason_names(0) == []


def test_valid_volume():
    assert u._valid_volume("C:")
    assert u._valid_volume("D:")
    assert not u._valid_volume("C")
    assert not u._valid_volume("C:\\")
    assert not u._valid_volume("")
    assert not u._valid_volume("CC:")


def test_recent_file_changes_rejects_bad_volume():
    r = u.recent_file_changes(volume="bad")
    assert "error" in r


def test_directory_churn_rejects_bad_volume():
    assert "error" in u.directory_churn(volume="X")


# --- real-data smoke (skips cleanly if not elevated) -----------------------
def test_usn_status_real():
    s = u.usn_status()
    if "error" in s:
        assert "admin" in s["error"].lower()
        return
    assert "journal_id" in s and "next_usn" in s and s["span_bytes"] >= 0


def test_recent_file_changes_real():
    r = u.recent_file_changes(minutes=120, max=5)
    if "error" in r:
        assert "admin" in r["error"].lower()
        return
    assert "changes" in r and isinstance(r["changes"], list)
    for c in r["changes"]:
        assert "time" in c and "reasons" in c and isinstance(c["reasons"], list)


def test_directory_churn_real():
    r = u.directory_churn(minutes=120, top_n=5)
    if "error" in r:
        return
    assert "directories" in r
    for d in r["directories"]:
        assert "directory" in d and d["change_count"] >= 1
