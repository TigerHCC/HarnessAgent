import struct
import prefetch_reader as pf


def test_ft_conversion():
    assert pf._ft_to_iso(0) is None
    assert pf._ft_to_iso(None) is None
    assert pf._ft_to_iso(9 * 10 ** 20) is None      # overflow guard, must not raise
    iso = pf._ft_to_iso(134282000051279120)
    assert iso and iso.startswith("2026")


def test_safe_pf_name():
    assert pf._safe_pf_name("CMD.EXE-1234.pf") == "CMD.EXE-1234.pf"
    assert pf._safe_pf_name("CMD.EXE-1234") == "CMD.EXE-1234.pf"  # appends .pf
    assert pf._safe_pf_name("..") is None
    assert pf._safe_pf_name(".") is None
    assert pf._safe_pf_name("a\\b.pf") is None
    assert pf._safe_pf_name("a/b.pf") is None
    assert pf._safe_pf_name("") is None


def test_decompress_rejects_non_mam():
    try:
        pf._decompress(b"NOTMAM__" + b"\x00" * 16)
        assert False, "should have raised"
    except ValueError:
        pass


def test_decompress_rejects_huge_size():
    bad = b"MAM\x04" + struct.pack("<I", 999_999_999) + b"\x00" * 8
    try:
        pf._decompress(bad)
        assert False
    except ValueError:
        pass


def test_parse_scca_rejects_non_scca():
    try:
        pf._parse_scca(b"\x00" * 0x200)
        assert False
    except ValueError:
        pass


# --- real-data smoke (skips cleanly if Prefetch is empty / not elevated) ----
def test_prefetch_list_real():
    res = pf.prefetch_list(max=5)
    if "error" in res:
        assert "admin" in res["error"].lower() or "directory" in res["error"].lower()
        return
    assert "prefetch" in res and isinstance(res["prefetch"], list)
    for r in res["prefetch"]:
        if "parse_error" in r:
            continue
        assert "exe" in r and "hash" in r


def test_prefetch_detail_real():
    lst = pf.prefetch_list(max=1)
    if "error" in lst or not lst.get("prefetch"):
        return
    first = next((r for r in lst["prefetch"] if "pf_file" in r and "parse_error" not in r), None)
    if not first:
        return
    d = pf.prefetch_detail(first["pf_file"])
    if "error" in d:
        return
    assert d["exe"]
    assert isinstance(d["run_times"], list)
    assert "files" in d or "file_count" in d or "run_count" in d
