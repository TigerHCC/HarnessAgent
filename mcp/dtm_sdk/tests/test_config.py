# mcp/dtm_sdk/tests/test_config.py
import json
import os
import config


def _write(tmp_path, obj):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    return str(p)


def test_var_expansion(tmp_path):
    p = _write(tmp_path, {"samples_root": "R", "executables": {"dtmutil": "${samples_root}/a.exe"},
                          "datatype_tables": {}, "howto": "", "timeout_seconds": 120,
                          "timeout_overrides": {}})
    cfg = config.load(p)
    assert cfg["executables"]["dtmutil"] == "R/a.exe"


def test_env_override(tmp_path, monkeypatch):
    p = _write(tmp_path, {"samples_root": "R", "executables": {"dtmutil": "${samples_root}/a.exe"},
                          "datatype_tables": {}, "howto": "", "timeout_seconds": 120,
                          "timeout_overrides": {}})
    monkeypatch.setenv("DTM_SDK_SAMPLES_ROOT", "OVERRIDE")
    cfg = config.load(p)
    assert cfg["executables"]["dtmutil"] == "OVERRIDE/a.exe"


def test_timeout_env_override_is_int(tmp_path, monkeypatch):
    p = _write(tmp_path, {"samples_root": "R", "executables": {}, "datatype_tables": {}, "howto": "",
                          "timeout_seconds": 120, "timeout_overrides": {}})
    monkeypatch.setenv("DTM_SDK_TIMEOUT_SECONDS", "300")
    cfg = config.load(p)
    assert cfg["timeout_seconds"] == 300


def test_resolved_map_reports_existence(tmp_path):
    real = tmp_path / "a.exe"
    real.write_text("x", encoding="utf-8")
    p = _write(tmp_path, {"samples_root": str(tmp_path), "executables": {"dtmutil": "${samples_root}/a.exe",
               "analytics": "${samples_root}/missing.exe"}, "datatype_tables": {}, "howto": "",
               "timeout_seconds": 120, "timeout_overrides": {}})
    cfg = config.load(p)
    assert cfg["_resolved"]["executables.dtmutil"]["exists"] is True
    assert cfg["_resolved"]["executables.analytics"]["exists"] is False


def test_default_client_id_passthrough(tmp_path):
    p = _write(tmp_path, {"samples_root": "R", "executables": {}, "datatype_tables": {}, "howto": "",
                          "timeout_seconds": 120, "timeout_overrides": {},
                          "default_client_id": "675f1370-b7ce-4113-8d6e-a128ee3bb74b",
                          "default_client_name": None})
    cfg = config.load(p)
    assert cfg["default_client_id"] == "675f1370-b7ce-4113-8d6e-a128ee3bb74b"
    # id alone (no name) is valid -- it is a default, not a mandatory pair
    assert cfg.get("default_client_name") is None


def test_default_client_id_env_override(tmp_path, monkeypatch):
    p = _write(tmp_path, {"samples_root": "R", "executables": {}, "datatype_tables": {}, "howto": "",
                          "timeout_seconds": 120, "timeout_overrides": {},
                          "default_client_id": "675f1370-b7ce-4113-8d6e-a128ee3bb74b",
                          "default_client_name": None})
    monkeypatch.setenv("DTM_SDK_DEFAULT_CLIENT_ID", "aaaaaaaa-0000-0000-0000-000000000000")
    cfg = config.load(p)
    assert cfg["default_client_id"] == "aaaaaaaa-0000-0000-0000-000000000000"


def test_env_key():
    assert config.env_key("samples_root") == "DTM_SDK_SAMPLES_ROOT"
    assert config.env_key("timeout_seconds") == "DTM_SDK_TIMEOUT_SECONDS"
    assert config.env_key("default_client_id") == "DTM_SDK_DEFAULT_CLIENT_ID"
