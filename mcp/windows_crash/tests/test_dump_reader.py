import os
import struct
import dump_reader as d


def _make_kernel_header():
    """Build a minimal valid DUMP_HEADER64 (PAGEDU64) with a known bugcheck."""
    buf = bytearray(0x60)
    buf[0:8] = b"PAGEDU64"
    struct.pack_into("<II", buf, 0x08, 15, 26100)          # major, minor(build)
    struct.pack_into("<II", buf, 0x30, 0x8664, 8)          # machine x64, 8 processors
    struct.pack_into("<I", buf, 0x38, 0x133)               # bugcheck DPC_WATCHDOG_VIOLATION
    struct.pack_into("<QQQQ", buf, 0x40, 1, 2, 3, 4)       # params
    return bytes(buf)


def test_parse_kernel_header():
    rec = d._parse_kernel_header(_make_kernel_header(), bits64=True)
    assert rec["kind"] == "kernel"
    assert rec["bugcheck_code"] == "0x00000133"
    assert rec["bugcheck_name"] == "DPC_WATCHDOG_VIOLATION"
    assert rec["build"] == 26100
    assert rec["machine"] == "x64"
    assert rec["processors"] == 8
    assert len(rec["parameters"]) == 4
    assert "layout_uncertain" not in rec


def test_analyze_dump_path_containment():
    # a path outside the allowed dump dirs must be refused
    r = d.analyze_dump("C:\\Windows\\System32\\notepad.exe")
    assert "error" in r and "not allowed" in r["error"]
    r2 = d.analyze_dump("")
    assert "error" in r2


def test_analyze_real_kernel_dump(tmp_path):
    """If C:\\Windows\\Minidump has a dump, analyze it (needs admin to read)."""
    dumps = d.list_dumps()
    assert "counts" in dumps
    minis = dumps.get("minidumps", [])
    if not minis:
        return  # no dumps on this box
    path = minis[0]["path"]
    rec = d.analyze_dump(path)
    if "error" in rec:
        # acceptable outcomes: needs elevation, or unreadable — but not a crash
        assert "permission" in rec["error"].lower() or "not allowed" in rec["error"].lower() \
            or "short" in rec["error"].lower()
        return
    assert rec["kind"] in ("kernel", "user")
    if rec["kind"] == "kernel":
        assert rec["bugcheck_code"].startswith("0x")
        assert isinstance(rec["build"], int)


def _build_mdmp(module_name="ntdll.dll", exc_code=0xC0000005, name_len=None, nmods=1):
    """Build a minimal but valid MDMP with one module + an exception stream."""
    HDR = 0x20
    DIR = HDR                      # stream directory at 0x20
    MODLIST = DIR + 24             # after 2 dir entries (12B each)
    name_rva = MODLIST + 4 + 108   # after NumberOfModules + one MINIDUMP_MODULE
    name_utf16 = module_name.encode("utf-16-le")
    nlen = name_len if name_len is not None else len(name_utf16)
    EXC = name_rva + 4 + len(name_utf16) + 8
    size = EXC + 64
    b = bytearray(size)
    struct.pack_into("<4sIII", b, 0, b"MDMP", 0xA793, 2, DIR)   # sig, ver, nstreams, dir_rva
    struct.pack_into("<I", b, 0x14, 0)                           # timestamp
    struct.pack_into("<III", b, DIR, 4, 4 + 108, MODLIST)       # ModuleListStream
    struct.pack_into("<III", b, DIR + 12, 6, 168, EXC)          # ExceptionStream
    struct.pack_into("<I", b, MODLIST, nmods)                    # NumberOfModules
    struct.pack_into("<I", b, MODLIST + 4 + 20, name_rva)       # MINIDUMP_MODULE.ModuleNameRva @+20
    struct.pack_into("<I", b, name_rva, nlen)                    # MINIDUMP_STRING length (bytes)
    b[name_rva + 4: name_rva + 4 + len(name_utf16)] = name_utf16
    struct.pack_into("<I", b, EXC + 8, exc_code)                # ExceptionCode
    struct.pack_into("<Q", b, EXC + 24, 0x7FFDEADBEEF0)         # ExceptionAddress
    return bytes(b)


def test_parse_user_dump_module_and_exception():
    buf = _build_mdmp()
    rec = d._parse_user_dump(buf, len(buf))
    assert rec["kind"] == "user"
    # module name resolves (regression: offset must be base+20, not base+24)
    assert rec["modules"] == ["ntdll.dll"], rec
    assert rec["module_count"] == 1
    assert rec["exception_code"] == "0xC0000005"
    assert rec["code_meaning"] == "ACCESS_VIOLATION"
    assert rec["exception_address"].startswith("0x")


def test_read_minidump_string_bounds():
    buf = _build_mdmp(name_len=0xFFFFFFF0)  # hostile huge length -> must clamp, not blow up
    rec = d._parse_user_dump(buf, len(buf))
    # name gets clamped to remaining bytes; no exception, still a bounded string
    assert isinstance(rec.get("modules"), list)
    # rva past EOF -> None
    assert d._read_minidump_string(buf, len(buf) + 1000, len(buf)) is None
    assert d._read_minidump_string(buf, 0, 0) is None


def test_modules_truncated_flag():
    # 700 modules but the block only has room parsed; module_count reflects claim, flag set
    buf = _build_mdmp(nmods=700)
    rec = d._parse_user_dump(buf, len(buf))
    assert rec["module_count"] == 700
    assert rec.get("modules_truncated") is True


def test_parse_kernel_header_32bit():
    buf = bytearray(0x60)
    buf[0:8] = b"PAGEDUMP"
    struct.pack_into("<II", buf, 0x08, 15, 7601)      # major, build 7601 (Win7)
    struct.pack_into("<II", buf, 0x20, 0x014C, 4)     # machine x86, nproc @ 32-bit offsets
    struct.pack_into("<I", buf, 0x28, 0x0A)           # bugcheck IRQL_NOT_LESS_OR_EQUAL @0x28
    struct.pack_into("<IIII", buf, 0x2C, 1, 2, 3, 4)
    rec = d._parse_kernel_header(bytes(buf), bits64=False)
    assert rec["bugcheck_name"] == "IRQL_NOT_LESS_OR_EQUAL"
    assert rec["machine"] == "x86"
    assert rec["processors"] == 4
    assert rec["build"] == 7601


def test_resolve_allowed_rejects_outside():
    assert d._resolve_allowed("C:\\Windows\\System32\\notepad.exe") is None
    assert d._resolve_allowed("") is None


def test_cdb_available_is_bool():
    assert isinstance(d.cdb_available(), bool)


def test_health_shape():
    h = d.health()
    for k in ("is_admin", "cdb_available", "counts"):
        assert k in h
