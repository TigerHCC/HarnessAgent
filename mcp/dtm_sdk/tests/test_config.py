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
                          "timeout_overrides": {}, "app_id": None, "app_name": None})
    cfg = config.load(p)
    assert cfg["executables"]["dtmutil"] == "R/a.exe"


def test_env_override(tmp_path, monkeypatch):
    p = _write(tmp_path, {"samples_root": "R", "executables": {"dtmutil": "${samples_root}/a.exe"},
                          "datatype_tables": {}, "howto": "", "timeout_seconds": 120,
                          "timeout_overrides": {}, "app_id": None, "app_name": None})
    monkeypatch.setenv("DTM_SDK_SAMPLES_ROOT", "OVERRIDE")
    cfg = config.load(p)
    assert cfg["executables"]["dtmutil"] == "OVERRIDE/a.exe"


def test_timeout_env_override_is_int(tmp_path, monkeypatch):
    p = _write(tmp_path, {"samples_root": "R", "executables": {}, "datatype_tables": {}, "howto": "",
                          "timeout_seconds": 120, "timeout_overrides": {}, "app_id": None, "app_name": None})
    monkeypatch.setenv("DTM_SDK_TIMEOUT_SECONDS", "300")
    cfg = config.load(p)
    assert cfg["timeout_seconds"] == 300


def test_resolved_map_reports_existence(tmp_path):
    real = tmp_path / "a.exe"
    real.write_text("x", encoding="utf-8")
    p = _write(tmp_path, {"samples_root": str(tmp_path), "executables": {"dtmutil": "${samples_root}/a.exe",
               "analytics": "${samples_root}/missing.exe"}, "datatype_tables": {}, "howto": "",
               "timeout_seconds": 120, "timeout_overrides": {}, "app_id": None, "app_name": None})
    cfg = config.load(p)
    assert cfg["_resolved"]["executables.dtmutil"]["exists"] is True
    assert cfg["_resolved"]["executables.analytics"]["exists"] is False


def test_appid_without_appname_raises(tmp_path):
    p = _write(tmp_path, {"samples_root": "R", "executables": {}, "datatype_tables": {}, "howto": "",
                          "timeout_seconds": 120, "timeout_overrides": {}, "app_id": "abc", "app_name": None})
    try:
        config.load(p)
        assert False, "expected ConfigError"
    except config.ConfigError:
        pass


def test_env_key():
    assert config.env_key("samples_root") == "DTM_SDK_SAMPLES_ROOT"
    assert config.env_key("timeout_seconds") == "DTM_SDK_TIMEOUT_SECONDS"
