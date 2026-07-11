import os
import wer_reader as w

# A synthetic APPCRASH Report.wer mirroring the real on-disk format:
# UTF-16, localized (Chinese) Sig[].Name, English keys, FILETIME EventTime.
APPCRASH = "\n".join([
    "Version=1",
    "EventType=APPCRASH",
    "EventTime=134281399317094087",
    "OriginalFilename=Acrobat.exe",
    "IsFatal=1",
    "Response.BucketId=8193f1bc31fbb030246a69c1fa624bb2",
    "FriendlyEventName=已停止運作",
    "AppPath=C:\\Program Files\\Adobe\\Acrobat DC\\Acrobat\\Acrobat.exe",
    "Sig[0].Name=應用程式名稱",
    "Sig[0].Value=Acrobat.exe",
    "Sig[1].Name=應用程式版本",
    "Sig[1].Value=25.1.20577.0",
    "Sig[2].Name=x",
    "Sig[2].Value=6876ca80",
    "Sig[3].Name=錯誤模組名稱",
    "Sig[3].Value=ntdll.dll",
    "Sig[4].Name=x",
    "Sig[4].Value=10.0.26100.4652",
    "Sig[5].Name=x",
    "Sig[5].Value=6c6bd922",
    "Sig[6].Name=例外狀況代碼",
    "Sig[6].Value=c0000005",
    "Sig[7].Name=x",
    "Sig[7].Value=0000000000039463",
    "OsInfo[0].Key=vermaj",
    "OsInfo[0].Value=10",
    "",
])

BEX64 = "\n".join([
    "Version=1",
    "EventType=BEX64",
    "OriginalFilename=foo.exe",
    "Sig[0].Value=foo.exe",
    "Sig[3].Value=bar.dll",
    "Sig[6].Value=0000000000001234",   # offset
    "Sig[7].Value=c0000409",           # exception code (swapped vs APPCRASH)
    "",
])


def _write(tmp_path, name, content):
    folder = tmp_path / name
    folder.mkdir()
    p = folder / "Report.wer"
    p.write_text(content, encoding="utf-16")
    return str(folder), str(p)


def test_parse_appcrash_typed(tmp_path):
    folder, wer = _write(tmp_path, "AppCrash_Acrobat.exe_deadbeef", APPCRASH)
    rec = w._report_record("test", folder, wer, full=True)
    assert rec["event_type"] == "APPCRASH"
    assert rec["app"] == "Acrobat.exe"
    assert rec["faulting_module"] == "ntdll.dll"
    assert rec["exception_code"] == "c0000005"
    assert rec["code_meaning"] == "ACCESS_VIOLATION"
    assert rec["report_id"] == "AppCrash_Acrobat.exe_deadbeef"
    # localized non-ASCII Sig name survived the UTF-16 decode
    names = [s["name"] for s in rec["signatures"]]
    assert any(n and "代碼" in n for n in names)  # 代碼
    assert rec["parsed"]["module_version"] == "10.0.26100.4652"


def test_bex64_code_position(tmp_path):
    folder, wer = _write(tmp_path, "BEX64_foo", BEX64)
    rec = w._report_record("test", folder, wer, full=True)
    # BEX swaps offset/code vs APPCRASH: code is at Sig[7]
    assert rec["exception_code"] == "c0000409"
    assert rec["code_meaning"] == "STACK_BUFFER_OVERRUN"
    assert rec["faulting_module"] == "bar.dll"


def test_filetime_conversion():
    when = w._filetime_to_dt("134281399317094087")
    assert when is not None
    assert when.year == 2026
    assert w._filetime_to_dt("0") is None
    assert w._filetime_to_dt(None) is None
    assert w._filetime_to_dt("garbage") is None


def test_filetime_overflow_returns_none():
    # a ~320-digit EventTime: int() succeeds but the division would raise OverflowError
    huge = "9" * 320
    assert w._filetime_to_dt(huge) is None  # must not raise


def test_poisoned_report_does_not_abort_scan(tmp_path):
    # a Report.wer with a monster EventTime must not crash get_crash/_report_record
    poison = APPCRASH.replace("EventTime=134281399317094087", "EventTime=" + "9" * 320)
    folder, wer = _write(tmp_path, "AppCrash_poison_deadbeef", poison)
    rec = w._report_record("test", folder, wer, full=True)
    assert "parse_error" not in rec
    assert rec["exception_code"] == "c0000005"   # still parses everything else
    assert isinstance(rec["time"], str)           # bad EventTime falls back to folder mtime


def test_is_crash_type():
    assert w.is_crash_type("APPCRASH")
    assert w.is_crash_type("BEX64")
    assert w.is_crash_type("AppHangB1")
    assert not w.is_crash_type("StoreAgentInstallFailure1")
    assert not w.is_crash_type("PerfWatsonVS12Data")
    assert not w.is_crash_type(None)


def test_get_crash_rejects_path_traversal():
    r = w.get_crash("..\\..\\evil")
    assert "error" in r
    r2 = w.get_crash("a/b")
    assert "error" in r2


def test_get_crash_rejects_dot_and_dotdot():
    assert "error" in w.get_crash(".")
    assert "error" in w.get_crash("..")
    assert "error" in w.get_crash("")


def test_list_crashes_max_zero_and_truncation():
    # max=0 must return zero rows (not one), with total_matching still counted
    res = w.list_crashes(days=3650, max=0)
    assert "error" not in res
    assert res["count"] == 0
    assert len(res["crashes"]) == 0
    assert res["total_matching"] >= 0
    if res["total_matching"] > 0:
        assert res["truncated"] is True
    # rows carry has_dump
    res2 = w.list_crashes(days=3650, max=5)
    for c in res2["crashes"]:
        assert "has_dump" in c and isinstance(c["has_dump"], bool)
    assert res2["truncated"] == (res2["total_matching"] > res2["count"])


# --- real-store smoke (skips cleanly if the machine has no WER store) -------
def test_health_shape():
    h = w.health()
    assert "is_admin" in h and "stores" in h
    assert isinstance(h["stores"], list)


def test_crash_summary_real_store():
    res = w.crash_summary(days=3650, top_n=10)
    assert "error" not in res, res
    assert "buckets" in res and isinstance(res["buckets"], list)
    for b in res["buckets"]:
        assert "count" in b and b["count"] >= 1
        assert "event_type" in b
