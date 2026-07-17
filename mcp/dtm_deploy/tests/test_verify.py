import os
import types

import pytest

import verify


def test_find_instrumentation_exe_missing(tmp_path):
    with pytest.raises(verify.VerifyError):
        verify.find_instrumentation_exe(str(tmp_path / "nope"))


def test_verify_collection_all_messages_found(monkeypatch, tmp_path):
    monkeypatch.setattr(verify, "find_instrumentation_exe", lambda p: "fake.exe")
    output = "\n".join(verify.EXPECTED_COLLECTION_MESSAGES)

    def fake_run(cmd, capture_output, text, timeout):
        return types.SimpleNamespace(returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(verify.subprocess, "run", fake_run)
    result = verify.verify_collection(str(tmp_path), datatype_name="CameraInfo")
    assert result["all_expected_found"] is True
    assert result["success"] is True


def test_verify_collection_missing_message(monkeypatch, tmp_path):
    monkeypatch.setattr(verify, "find_instrumentation_exe", lambda p: "fake.exe")

    def fake_run(cmd, capture_output, text, timeout):
        return types.SimpleNamespace(returncode=0, stdout="only some output", stderr="")

    monkeypatch.setattr(verify.subprocess, "run", fake_run)
    result = verify.verify_collection(str(tmp_path))
    assert result["all_expected_found"] is False
    assert result["success"] is False


def test_resolve_heartbeat_log_path_uses_explicit_path(tmp_path):
    f = tmp_path / "log.txt"
    f.write_text("x")
    assert verify.resolve_heartbeat_log_path(str(f)) == str(f)


def test_resolve_heartbeat_log_path_missing_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("ProgramData", str(tmp_path))
    with pytest.raises(verify.VerifyError):
        verify.resolve_heartbeat_log_path("")


def test_last_scheduler_index_finds_hb():
    lines = ["line0\n", "Started HB scheduler\n", "after1\n", "after2\n"]
    idx, has_hb, has_otp = verify._last_scheduler_index(lines)
    assert idx == 1
    assert has_hb is True
    assert has_otp is False


def test_poll_once_detects_hb_success(tmp_path):
    log = tmp_path / "log.txt"
    log.write_text(
        "Started HB scheduler\n"
        "Act:Response, Type:HB, Status:Success, RC:1:1:20\n",
        encoding="utf-8",
    )
    done, note = verify._poll_once(str(log))
    assert done is True
    assert "HB Response Success" in note


def test_poll_once_no_scheduler_yet(tmp_path):
    log = tmp_path / "log.txt"
    log.write_text("nothing interesting\n", encoding="utf-8")
    done, note = verify._poll_once(str(log))
    assert done is False
