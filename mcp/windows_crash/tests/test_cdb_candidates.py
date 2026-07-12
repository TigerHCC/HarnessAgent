"""cdb.exe lives in a per-architecture dir under the Windows Kits Debuggers folder.

The probe must pick only cdb binaries THIS process can actually execute (native first). Getting this
wrong in either direction is a real failure: on ARM64 we'd never find cdb at all (bugcheck decoding
silently degrades to the header-only parse), and if we naively probed every arch dir we could hand an
x64 box a cross-installed arm64 cdb.exe that it cannot run.

platform.machine() is the right predicate because it reports the *process* architecture, which is what
determines which exe we can exec -- an x64 Python emulated on an ARM64 host correctly reports AMD64 and
should launch the (also emulated) x64 cdb.
"""
import os
import platform

import dump_reader as d


def _dirs(cands):
    """The Debuggers arch dir of each Windows-Kits candidate, in probe order."""
    out = []
    for c in cands:
        parts = c.split(os.sep)
        if "Debuggers" in parts:
            out.append(parts[parts.index("Debuggers") + 1])
    return out


def test_arm64_probes_arm64_first_then_x64(monkeypatch):
    monkeypatch.setattr(platform, "machine", lambda: "ARM64")
    dirs = _dirs(d._cdb_candidates())
    assert "arm64" in dirs, "ARM64 host must probe Debuggers\\arm64 (this is the bug being fixed)"
    # x64 cdb runs under ARM64's emulation, so it's a legitimate fallback -- but only after native.
    assert "x64" in dirs
    assert dirs.index("arm64") < dirs.index("x64"), "native arm64 cdb must be preferred over emulated x64"


def test_amd64_never_offers_an_unrunnable_arm64_cdb(monkeypatch):
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    dirs = _dirs(d._cdb_candidates())
    assert dirs, "x64 host must still probe for cdb"
    assert set(dirs) == {"x64"}, (
        "an x64 process cannot exec an arm64 cdb.exe; probing that dir would break a box that has the "
        "cross-debugging tools installed. Got: %r" % (dirs,))


def test_amd64_candidates_are_unchanged_by_the_fix(monkeypatch):
    """Regression guard: on x64 the probe list must be byte-identical to the pre-fix behaviour."""
    monkeypatch.setattr(platform, "machine", lambda: "AMD64")
    expected = []
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if base:
            expected.append(os.path.join(base, "Windows Kits", "10", "Debuggers", "x64", "cdb.exe"))
            expected.append(os.path.join(base, "Windows Kits", "11", "Debuggers", "x64", "cdb.exe"))
    la = os.environ.get("LOCALAPPDATA")
    if la:
        expected.append(os.path.join(la, "Microsoft", "WindowsApps", "cdb.exe"))
    assert d._cdb_candidates() == expected


def test_unknown_arch_falls_back_to_x64(monkeypatch):
    monkeypatch.setattr(platform, "machine", lambda: "SOMETHING_NEW")
    assert set(_dirs(d._cdb_candidates())) == {"x64"}
