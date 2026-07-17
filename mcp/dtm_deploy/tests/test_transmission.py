import os

import pytest

import transmission


def test_find_dtmutil_exe_missing_download_path(tmp_path):
    with pytest.raises(transmission.TransmissionError):
        transmission.find_dtmutil_exe(str(tmp_path / "nope"))


def test_find_dtmutil_exe_resolves_newest(tmp_path):
    old_dir = tmp_path / "DTPSamples-1.0"
    new_dir = tmp_path / "DTPSamples-2.0"
    for d in (old_dir, new_dir):
        exe_dir = d / "Samples" / "DTMUtil" / "bin" / "Release"
        exe_dir.mkdir(parents=True)
        (exe_dir / "DTMUtil.exe").write_bytes(b"x")
    os.utime(old_dir, (1000, 1000))
    os.utime(new_dir, (2000, 2000))

    found = transmission.find_dtmutil_exe(str(tmp_path))
    assert found.startswith(str(new_dir))


def test_enable_transmission_raises_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(transmission, "_run_dtmutil", lambda exe, args, timeout=120: (1, "", "boom"))
    with pytest.raises(transmission.TransmissionError):
        transmission.enable_transmission("fake.exe")


def test_enable_transmission_success_without_notice(monkeypatch):
    monkeypatch.setattr(transmission, "_run_dtmutil", lambda exe, args, timeout=120: (0, "", ""))
    monkeypatch.setattr(transmission, "_installed_dtp_version", lambda: (3, 9, 0, 0))
    result = transmission.enable_transmission("fake.exe")
    assert result["realtime_transmission"] == "enabled"
    assert "N/A" in result["telemetry_notice"]


def test_enable_transmission_sets_notice_for_newer_dtp(monkeypatch):
    calls = []

    def fake_run(exe, args, timeout=120):
        calls.append(args)
        return 0, "", ""

    monkeypatch.setattr(transmission, "_run_dtmutil", fake_run)
    monkeypatch.setattr(transmission, "_installed_dtp_version", lambda: (4, 0, 0, 9406))
    result = transmission.enable_transmission("fake.exe")
    assert result["telemetry_notice"] == "set (1.0.0)"
    assert len(calls) == 2
