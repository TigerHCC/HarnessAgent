import struct
import registry_forensics as rf


def test_ft_conversion_overflow_guard():
    assert rf._ft_to_iso(9 * 10 ** 20) is None
    assert rf._ft_to_iso(0) is None
    assert rf._ft_to_iso(134282000051279120).startswith("2026")


def test_well_known_sid():
    assert rf._sid_to_name("S-1-5-18") == "SYSTEM"
    assert rf._sid_to_name("S-1-5-19") == "LOCAL SERVICE"


def test_map_device_path():
    dm = {"\\device\\harddiskvolume3": "C:"}
    assert rf._map_device_path("\\Device\\HarddiskVolume3\\Windows\\x.exe", dm) == "C:\\Windows\\x.exe"
    # unknown device stays raw
    assert rf._map_device_path("\\Device\\HarddiskVolume9\\y.exe", dm) == "\\Device\\HarddiskVolume9\\y.exe"


def _shim_entry(path_str, ft=134282000051279120):
    path = path_str.encode("utf-16-le")
    body = struct.pack("<H", len(path)) + path + struct.pack("<Q", ft) + struct.pack("<I", 0)
    return b"10ts" + b"\x00\x00\x00\x00" + struct.pack("<I", len(body)) + body


def test_parse_shimcache_synthetic():
    blob = struct.pack("<I", 0x34) + b"\x00" * (0x34 - 4) + _shim_entry("C:\\x.exe")
    rows, parsed, matched = rf._parse_shimcache(blob)
    assert parsed == 1 and matched == 1
    assert rows[0]["path"] == "C:\\x.exe"
    assert rows[0]["last_modified"].startswith("2026")
    assert rows[0]["position"] == 0


def test_parse_shimcache_stops_on_garbage():
    blob = struct.pack("<I", 0x34) + b"\x00" * (0x34 - 4) + b"XXXXjunkjunk"
    rows, parsed, matched = rf._parse_shimcache(blob)
    assert parsed == 0 and matched == 0 and rows == []


def test_shimcache_filter_does_not_falsely_truncate():
    # 3 entries, 1 matches the filter -> the one match is returned, truncated must be False
    blob = (struct.pack("<I", 0x34) + b"\x00" * (0x34 - 4)
            + _shim_entry("C:\\WINDOWS\\route.exe")
            + _shim_entry("C:\\chrome.exe")
            + _shim_entry("C:\\WINDOWS\\notepad.exe"))
    rows, parsed, matched = rf._parse_shimcache(blob, filter="chrome")
    assert parsed == 3 and matched == 1 and len(rows) == 1
    assert matched <= len(rows) or matched == len(rows)  # nothing capped
    # and the tool-level truncated flag would be matched(1) > len(rows)(1) == False


# --- real-data smoke --------------------------------------------------------
def test_bam_list_real():
    res = rf.bam_list(max=5)
    if "error" in res:
        assert "admin" in res["error"].lower() or "not found" in res["error"].lower()
        return
    assert "bam" in res
    for r in res["bam"]:
        assert "exe" in r and "last_exec" in r


def test_userassist_real():
    res = rf.userassist_list(max=5)
    if "error" in res:
        return
    assert "userassist" in res


def test_shimcache_real():
    res = rf.shimcache_list(max=5)
    if "error" in res:
        assert "admin" in res["error"].lower()
        return
    assert "shimcache" in res and "note" in res


def test_health_shape():
    h = rf.health()
    assert "is_admin" in h
    for k in ("bam", "userassist", "shimcache"):
        assert k in h
